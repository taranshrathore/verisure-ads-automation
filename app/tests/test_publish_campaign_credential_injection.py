"""Credential-injection seam tests for PublishCampaignService.

Covers the end-to-end local flow:

ProviderConnection -> CredentialEncryptionService ->
ProviderConnectionService.get_decrypted_credentials() ->
PublishCampaignService -> Adapter.publish(spec, credentials)

No real Meta/Google OAuth or HTTP. Fake adapters capture the exact
ProviderCredentials they receive. Missing connections and decryption
failures mark only that provider's deployment FAILED and continue.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.adapters.base_adapter import BaseAdapter
from app.adapters.models import PublishResult
from app.core.campaign_spec import CampaignSpec
from app.core.exceptions import CredentialDecryptionError
from app.core.provider_credentials import ProviderCredentials
from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService
from app.models.campaign import Campaign, CampaignBudgetType, CampaignObjective
from app.models.campaign_deployment import CampaignDeploymentStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.provider_connection_repository import (
    ProviderConnectionRepository,
)
from app.services.campaign_deployment_service import CampaignDeploymentService
from app.services.campaign_spec_builder import CampaignSpecBuilder
from app.services.provider_connection_service import ProviderConnectionService
from app.services.publish_campaign_service import PublishCampaignService

_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
_META_PAYLOAD = b"meta-secret-credential-bytes"
_GOOGLE_PAYLOAD = b"google-secret-credential-bytes"


class CapturingSuccessAdapter(BaseAdapter):
    """Records every publish call's credentials argument and succeeds."""

    def __init__(self, external_campaign_id: str) -> None:
        self._external_campaign_id = external_campaign_id
        self.received_credentials: list[ProviderCredentials] = []
        self.received_args: list[tuple[object, object]] = []

    def publish(
        self, spec: CampaignSpec, credentials: ProviderCredentials
    ) -> PublishResult:
        self.received_credentials.append(credentials)
        self.received_args.append((spec, credentials))
        return PublishResult(
            success=True,
            external_campaign_id=self._external_campaign_id,
            error_message=None,
        )

    def pause(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by these tests.")

    def resume(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by these tests.")


class FakeAdapterRegistry:
    def __init__(self, adapters: dict[Provider, BaseAdapter]) -> None:
        self._adapters = adapters

    def get(self, provider: Provider) -> BaseAdapter:
        return self._adapters[provider]


class _FailingDecryptConnectionService:
    """Duck-typed ProviderConnectionService that fails decrypt for one
    provider and delegates everything else to a real instance.
    """

    def __init__(
        self,
        real: ProviderConnectionService,
        *,
        failing_provider: Provider,
    ) -> None:
        self._real = real
        self._failing_provider = failing_provider

    def get_decrypted_credentials(
        self, *, tenant_id, provider: Provider
    ) -> bytes:
        if provider == self._failing_provider:
            raise CredentialDecryptionError()
        return self._real.get_decrypted_credentials(
            tenant_id=tenant_id, provider=provider
        )


@pytest.fixture
def deployment_repository(db_session: Session) -> CampaignDeploymentRepository:
    return CampaignDeploymentRepository(db_session)


@pytest.fixture
def deployment_service(
    deployment_repository: CampaignDeploymentRepository, db_session: Session
) -> CampaignDeploymentService:
    return CampaignDeploymentService(deployment_repository, db_session)


@pytest.fixture
def connection_service(db_session: Session) -> ProviderConnectionService:
    return ProviderConnectionService(
        ProviderConnectionRepository(db_session),
        CredentialEncryptionService(_TEST_ENCRYPTION_KEY),
        db_session,
    )


def _make_tenant_and_user(db_session: Session, *, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"Cred Inject Tenant {suffix}", slug=f"cred-inject-{suffix}"
    )
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"cred-inject-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    return tenant, user


def _make_complete_campaign(
    db_session: Session, tenant: Tenant, user: User
) -> Campaign:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name="Credential injection campaign",
        objective=CampaignObjective.CONVERSIONS,
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("75.00"),
        currency="USD",
        start_at=now,
        end_at=now + timedelta(days=30),
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


def _by_provider(deployments: list, provider: Provider):
    return next(d for d in deployments if d.provider == provider)


def _make_publish_service(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
    adapter_registry: FakeAdapterRegistry,
) -> PublishCampaignService:
    return PublishCampaignService(
        CampaignRepository(db_session),
        deployment_repository,
        deployment_service,
        CampaignSpecBuilder(),
        adapter_registry,  # type: ignore[arg-type]
        connection_service,  # type: ignore[arg-type]
        db_session,
    )


# --- ProviderCredentials value object ------------------------------------------


def test_provider_credentials_repr_never_leaks_payload() -> None:
    secret = b"super-secret-token-value-do-not-leak"
    credentials = ProviderCredentials(
        provider=Provider.META, credential_payload=secret
    )

    rendered = repr(credentials)

    assert secret not in rendered.encode("utf-8")
    assert "super-secret" not in rendered
    assert str(len(secret)) not in rendered
    assert "credential_payload=<redacted>" in rendered
    assert "Provider.META" in rendered or "meta" in rendered


def test_provider_credentials_is_frozen() -> None:
    credentials = ProviderCredentials(
        provider=Provider.GOOGLE, credential_payload=b"x"
    )
    with pytest.raises(Exception):
        credentials.credential_payload = b"y"  # type: ignore[misc]


# --- Credentials injected into adapter ----------------------------------------


def test_credentials_injected_into_adapter_exactly(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="exact")
    campaign = _make_complete_campaign(db_session, tenant, user)
    connection_service.connect(
        tenant_id=tenant.id,
        provider=Provider.META,
        credential_payload=_META_PAYLOAD,
    )
    connection_service.connect(
        tenant_id=tenant.id,
        provider=Provider.GOOGLE,
        credential_payload=_GOOGLE_PAYLOAD,
    )

    meta_adapter = CapturingSuccessAdapter("meta-ext-cred-1")
    google_adapter = CapturingSuccessAdapter("google-ext-cred-1")
    service = _make_publish_service(
        deployment_repository,
        deployment_service,
        connection_service,
        db_session,
        FakeAdapterRegistry(
            {Provider.META: meta_adapter, Provider.GOOGLE: google_adapter}
        ),
    )

    deployments = service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )

    assert all(
        d.status == CampaignDeploymentStatus.SUBMITTED for d in deployments
    )
    assert len(meta_adapter.received_credentials) == 1
    assert len(google_adapter.received_credentials) == 1

    meta_creds = meta_adapter.received_credentials[0]
    google_creds = google_adapter.received_credentials[0]

    assert isinstance(meta_creds, ProviderCredentials)
    assert isinstance(google_creds, ProviderCredentials)
    assert meta_creds == ProviderCredentials(
        provider=Provider.META, credential_payload=_META_PAYLOAD
    )
    assert google_creds == ProviderCredentials(
        provider=Provider.GOOGLE, credential_payload=_GOOGLE_PAYLOAD
    )


def test_adapters_never_receive_raw_bytes_directly(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="raw")
    campaign = _make_complete_campaign(db_session, tenant, user)
    connection_service.connect(
        tenant_id=tenant.id,
        provider=Provider.META,
        credential_payload=_META_PAYLOAD,
    )
    connection_service.connect(
        tenant_id=tenant.id,
        provider=Provider.GOOGLE,
        credential_payload=_GOOGLE_PAYLOAD,
    )

    meta_adapter = CapturingSuccessAdapter("meta-ext-raw")
    google_adapter = CapturingSuccessAdapter("google-ext-raw")
    service = _make_publish_service(
        deployment_repository,
        deployment_service,
        connection_service,
        db_session,
        FakeAdapterRegistry(
            {Provider.META: meta_adapter, Provider.GOOGLE: google_adapter}
        ),
    )

    service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    for adapter in (meta_adapter, google_adapter):
        assert len(adapter.received_args) == 1
        spec_arg, credentials_arg = adapter.received_args[0]
        assert isinstance(spec_arg, CampaignSpec)
        assert isinstance(credentials_arg, ProviderCredentials)
        assert not isinstance(credentials_arg, bytes)
        assert type(credentials_arg) is ProviderCredentials


# --- Missing connection / decryption failure ----------------------------------


def test_missing_provider_connection_marks_deployment_failed(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="missing")
    campaign = _make_complete_campaign(db_session, tenant, user)
    # No ProviderConnection rows created.

    meta_adapter = CapturingSuccessAdapter("meta-should-not-run")
    google_adapter = CapturingSuccessAdapter("google-should-not-run")
    service = _make_publish_service(
        deployment_repository,
        deployment_service,
        connection_service,
        db_session,
        FakeAdapterRegistry(
            {Provider.META: meta_adapter, Provider.GOOGLE: google_adapter}
        ),
    )

    deployments = service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )

    assert len(deployments) == 2
    for deployment in deployments:
        assert deployment.status == CampaignDeploymentStatus.FAILED
        assert deployment.last_error_message is not None
        assert deployment.last_error_message == "Authentication with provider failed."
        assert "not found" not in deployment.last_error_message.lower()
    assert meta_adapter.received_credentials == []
    assert google_adapter.received_credentials == []


def test_decryption_failure_marks_deployment_failed(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="decrypt")
    campaign = _make_complete_campaign(db_session, tenant, user)
    connection_service.connect(
        tenant_id=tenant.id,
        provider=Provider.META,
        credential_payload=_META_PAYLOAD,
    )
    connection_service.connect(
        tenant_id=tenant.id,
        provider=Provider.GOOGLE,
        credential_payload=_GOOGLE_PAYLOAD,
    )

    failing_connections = _FailingDecryptConnectionService(
        connection_service, failing_provider=Provider.META
    )
    meta_adapter = CapturingSuccessAdapter("meta-should-not-run")
    google_adapter = CapturingSuccessAdapter("google-ext-after-decrypt-fail")
    service = _make_publish_service(
        deployment_repository,
        deployment_service,
        failing_connections,  # type: ignore[arg-type]
        db_session,
        FakeAdapterRegistry(
            {Provider.META: meta_adapter, Provider.GOOGLE: google_adapter}
        ),
    )

    deployments = service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )

    meta = _by_provider(deployments, Provider.META)
    google = _by_provider(deployments, Provider.GOOGLE)
    assert meta.status == CampaignDeploymentStatus.FAILED
    assert meta.last_error_message is not None
    assert meta_adapter.received_credentials == []
    assert google.status == CampaignDeploymentStatus.SUBMITTED
    assert google.external_campaign_id == "google-ext-after-decrypt-fail"
    assert len(google_adapter.received_credentials) == 1


# --- Isolation across providers -----------------------------------------------


def test_meta_credential_failure_still_attempts_google(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="meta-fail")
    campaign = _make_complete_campaign(db_session, tenant, user)
    # Only Google is connected; Meta missing -> FAILED, Google still runs.
    connection_service.connect(
        tenant_id=tenant.id,
        provider=Provider.GOOGLE,
        credential_payload=_GOOGLE_PAYLOAD,
    )

    meta_adapter = CapturingSuccessAdapter("meta-should-not-run")
    google_adapter = CapturingSuccessAdapter("google-ext-meta-missing")
    service = _make_publish_service(
        deployment_repository,
        deployment_service,
        connection_service,
        db_session,
        FakeAdapterRegistry(
            {Provider.META: meta_adapter, Provider.GOOGLE: google_adapter}
        ),
    )

    deployments = service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )

    meta = _by_provider(deployments, Provider.META)
    google = _by_provider(deployments, Provider.GOOGLE)
    assert meta.status == CampaignDeploymentStatus.FAILED
    assert meta_adapter.received_credentials == []
    assert google.status == CampaignDeploymentStatus.SUBMITTED
    assert google_adapter.received_credentials[0].credential_payload == _GOOGLE_PAYLOAD


def test_google_credential_failure_still_attempts_meta(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="google-fail")
    campaign = _make_complete_campaign(db_session, tenant, user)
    connection_service.connect(
        tenant_id=tenant.id,
        provider=Provider.META,
        credential_payload=_META_PAYLOAD,
    )
    # Google missing.

    meta_adapter = CapturingSuccessAdapter("meta-ext-google-missing")
    google_adapter = CapturingSuccessAdapter("google-should-not-run")
    service = _make_publish_service(
        deployment_repository,
        deployment_service,
        connection_service,
        db_session,
        FakeAdapterRegistry(
            {Provider.META: meta_adapter, Provider.GOOGLE: google_adapter}
        ),
    )

    deployments = service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )

    meta = _by_provider(deployments, Provider.META)
    google = _by_provider(deployments, Provider.GOOGLE)
    assert meta.status == CampaignDeploymentStatus.SUBMITTED
    assert meta_adapter.received_credentials[0].credential_payload == _META_PAYLOAD
    assert google.status == CampaignDeploymentStatus.FAILED
    assert google_adapter.received_credentials == []
