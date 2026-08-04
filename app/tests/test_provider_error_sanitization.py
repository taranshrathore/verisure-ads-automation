"""Provider error sanitization tests.

Ensures secrets, stacks, and raw provider payloads never reach persistence
or API responses. No sleeps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.base_adapter import BaseAdapter
from app.adapters.models import PublishResult
from app.api.dependencies import get_publish_campaign_service
from app.core.campaign_spec import CampaignSpec
from app.core.exceptions import (
    CredentialDecryptionError,
    ProviderConnectionNotFoundError,
)
from app.core.provider_credentials import ProviderCredentials
from app.core.provider_error_sanitization import (
    AUTHENTICATION_FAILED,
    PROVIDER_REQUEST_FAILED,
    RATE_LIMIT_EXCEEDED,
    TEMPORARY_PROVIDER_ERROR,
    UNEXPECTED_PROVIDER_ERROR,
    is_safe_provider_message,
    sanitize_provider_exception,
    sanitize_provider_message,
)
from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService
from app.main import app
from app.models.campaign import (
    Campaign,
    CampaignBudgetType,
    CampaignObjective,
)
from app.models.campaign_deployment import CampaignDeploymentStatus
from app.models.publish_job import PublishJobStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.provider_connection_repository import (
    ProviderConnectionRepository,
)
from app.repositories.publish_job_repository import PublishJobRepository
from app.services.campaign_deployment_service import CampaignDeploymentService
from app.services.campaign_spec_builder import CampaignSpecBuilder
from app.services.provider_connection_service import ProviderConnectionService
from app.services.publish_campaign_service import PublishCampaignService
from app.services.publish_job_service import PublishJobService

_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


# --- unit: sanitizer ---------------------------------------------------------


def test_raw_bearer_token_never_appears() -> None:
    message = sanitize_provider_exception(
        RuntimeError("Authorization: Bearer super-secret-token-value")
    )
    assert message == UNEXPECTED_PROVIDER_ERROR
    assert "Bearer" not in message
    assert "super-secret-token-value" not in message


def test_access_token_removed() -> None:
    message = sanitize_provider_message(
        'provider said access_token="ya29.a0AfH6SMB..." retry later'
    )
    assert message == UNEXPECTED_PROVIDER_ERROR
    assert "ya29" not in message
    assert "access_token" not in message


def test_client_secret_removed() -> None:
    message = sanitize_provider_message(
        "oauth failed client_secret=shhh-do-not-leak"
    )
    assert message == UNEXPECTED_PROVIDER_ERROR
    assert "shhh-do-not-leak" not in message


def test_jwt_removed() -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature"
    )
    message = sanitize_provider_message(f"token rejected: {jwt}")
    assert message == UNEXPECTED_PROVIDER_ERROR
    assert "eyJ" not in message
    assert jwt not in message


def test_db_url_removed() -> None:
    message = sanitize_provider_message(
        "could not mirror to postgresql://user:password@db:5432/verisure"
    )
    assert message == UNEXPECTED_PROVIDER_ERROR
    assert "postgresql://" not in message
    assert "password" not in message


def test_stack_trace_removed() -> None:
    stack = (
        "Traceback (most recent call last):\n"
        '  File "adapter.py", line 10, in publish\n'
        "RuntimeError: boom with secret=abc\n"
    )
    message = sanitize_provider_exception(RuntimeError(stack))
    assert message == UNEXPECTED_PROVIDER_ERROR
    assert "Traceback" not in message
    assert "adapter.py" not in message


def test_provider_json_removed() -> None:
    message = sanitize_provider_message(
        '{"error":{"message":"Invalid OAuth","type":"OAuthException","code":190}}'
    )
    assert message == UNEXPECTED_PROVIDER_ERROR
    assert "{" not in message
    assert "OAuthException" not in message


def test_auth_keyword_maps_to_authentication_failed() -> None:
    assert (
        sanitize_provider_message("401 Unauthorized from upstream")
        == AUTHENTICATION_FAILED
    )


def test_rate_limit_maps_to_safe_message() -> None:
    assert (
        sanitize_provider_message("429 Too Many Requests / rate limit")
        == RATE_LIMIT_EXCEEDED
    )


def test_timeout_maps_to_temporary() -> None:
    assert sanitize_provider_exception(TimeoutError()) == TEMPORARY_PROVIDER_ERROR


def test_connection_missing_maps_to_authentication_failed() -> None:
    assert (
        sanitize_provider_exception(ProviderConnectionNotFoundError())
        == AUTHENTICATION_FAILED
    )


def test_decryption_failure_maps_to_authentication_failed() -> None:
    assert (
        sanitize_provider_exception(CredentialDecryptionError())
        == AUTHENTICATION_FAILED
    )


def test_benign_provider_decline_is_generic_request_failed() -> None:
    assert (
        sanitize_provider_message("budget too low for this objective")
        == PROVIDER_REQUEST_FAILED
    )


def test_unknown_empty_becomes_unexpected() -> None:
    assert sanitize_provider_message("") == UNEXPECTED_PROVIDER_ERROR
    assert sanitize_provider_message(None) == UNEXPECTED_PROVIDER_ERROR


# --- integration: persistence + API ------------------------------------------


class _LeakyAdapter(BaseAdapter):
    def __init__(self, error: BaseException | None = None, message: str | None = None):
        self._error = error
        self._message = message

    def publish(
        self, spec: CampaignSpec, credentials: ProviderCredentials
    ) -> PublishResult:
        del spec, credentials
        if self._error is not None:
            raise self._error
        return PublishResult(
            success=False,
            external_campaign_id=None,
            error_message=self._message,
        )

    def pause(self, external_campaign_id: str) -> None:
        raise NotImplementedError

    def resume(self, external_campaign_id: str) -> None:
        raise NotImplementedError


class _FakeRegistry:
    def __init__(self, adapters: dict[Provider, BaseAdapter]) -> None:
        self._adapters = adapters

    def get(self, provider: Provider) -> BaseAdapter:
        return self._adapters[provider]


def _make_tenant_user_campaign(
    db_session: Session, *, suffix: str
) -> tuple[Tenant, User, Campaign]:
    tenant = Tenant(
        name=f"Sanitize Tenant {suffix}",
        slug=f"sanitize-{suffix}",
    )
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"sanitize-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name=f"Sanitize campaign {suffix}",
        objective=CampaignObjective.CONVERSIONS,
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("50.00"),
        currency="USD",
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    db_session.add(campaign)
    db_session.flush()
    return tenant, user, campaign


def _connect(connection_service: ProviderConnectionService, tenant_id) -> None:
    connection_service.connect(
        tenant_id=tenant_id,
        provider=Provider.META,
        credential_payload=b'{"access_token":"meta-token"}',
    )
    connection_service.connect(
        tenant_id=tenant_id,
        provider=Provider.GOOGLE,
        credential_payload=b'{"refresh_token":"google-token"}',
    )


def _publish_service(
    db_session: Session, registry: _FakeRegistry
) -> PublishCampaignService:
    encryption = CredentialEncryptionService(_TEST_ENCRYPTION_KEY)
    deployment_repository = CampaignDeploymentRepository(db_session)
    return PublishCampaignService(
        CampaignRepository(db_session),
        deployment_repository,
        CampaignDeploymentService(deployment_repository, db_session),
        CampaignSpecBuilder(),
        registry,  # type: ignore[arg-type]
        ProviderConnectionService(
            ProviderConnectionRepository(db_session), encryption, db_session
        ),
        db_session,
    )


def test_deployment_persists_sanitized_message(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="dep")
    connections = ProviderConnectionService(
        ProviderConnectionRepository(db_session),
        CredentialEncryptionService(_TEST_ENCRYPTION_KEY),
        db_session,
    )
    _connect(connections, tenant.id)
    service = _publish_service(
        db_session,
        _FakeRegistry(
            {
                Provider.META: _LeakyAdapter(
                    error=RuntimeError(
                        "Authorization: Bearer leak-token-please-hide"
                    )
                ),
                Provider.GOOGLE: _LeakyAdapter(
                    message='{"error":{"message":"fail","access_token":"x"}}'
                ),
            }
        ),
    )

    deployments = service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )
    for deployment in deployments:
        assert deployment.status == CampaignDeploymentStatus.FAILED
        assert is_safe_provider_message(deployment.last_error_message or "")
        assert "Bearer" not in (deployment.last_error_message or "")
        assert "leak-token" not in (deployment.last_error_message or "")
        assert "access_token" not in (deployment.last_error_message or "")
        assert "{" not in (deployment.last_error_message or "")


def test_publish_job_persists_sanitized_message(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="job")
    db_session.commit()

    class _BoomPublish:
        def publish_campaign(self, *, tenant_id, campaign_id, commit: bool = True):
            del tenant_id, campaign_id, commit
            raise RuntimeError(
                "provider dump access_token=ya29.secret client_secret=shh"
            )

    service = PublishJobService(
        PublishJobRepository(db_session),
        CampaignRepository(db_session),
        _BoomPublish(),  # type: ignore[arg-type]
        db_session,
    )
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    with pytest.raises(RuntimeError):
        service.run_once()

    loaded = PublishJobRepository(db_session).get_by_id(tenant.id, job.id)
    assert loaded is not None
    assert loaded.status == PublishJobStatus.FAILED
    assert is_safe_provider_message(loaded.error_message or "")
    assert "ya29" not in (loaded.error_message or "")
    assert "client_secret" not in (loaded.error_message or "")


def test_api_never_returns_sensitive_provider_text(
    db_session: Session, auth_fixture, client: TestClient
) -> None:
    user, token = auth_fixture()
    campaign = Campaign(
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        name="API sanitize campaign",
        objective=CampaignObjective.CONVERSIONS,
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("50.00"),
        currency="USD",
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    db_session.add(campaign)
    db_session.flush()

    connections = ProviderConnectionService(
        ProviderConnectionRepository(db_session),
        CredentialEncryptionService(_TEST_ENCRYPTION_KEY),
        db_session,
    )
    _connect(connections, user.tenant_id)
    service = _publish_service(
        db_session,
        _FakeRegistry(
            {
                Provider.META: _LeakyAdapter(
                    error=RuntimeError(
                        "Authorization: Bearer api-should-never-see-this"
                    )
                ),
                Provider.GOOGLE: _LeakyAdapter(
                    error=RuntimeError(
                        "Authorization: Bearer api-should-never-see-this"
                    )
                ),
            }
        ),
    )

    def _override_publish() -> PublishCampaignService:
        return service

    app.dependency_overrides[get_publish_campaign_service] = _override_publish
    try:
        service.publish_campaign(
            tenant_id=user.tenant_id, campaign_id=campaign.id
        )
        response = client.get(
            f"/api/v1/campaigns/{campaign.id}/deployments",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_publish_campaign_service, None)

    assert response.status_code == 200
    body = response.text
    assert "api-should-never-see-this" not in body
    assert "Authorization: Bearer api" not in body
    payload = response.json()
    assert "items" in payload
    for item in payload["items"]:
        assert is_safe_provider_message(item["last_error_message"])
