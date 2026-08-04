"""Integration tests for GET /api/v1/metrics."""

from __future__ import annotations

from datetime import datetime, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService
from app.models.campaign import Campaign, CampaignStatus
from app.models.publish_job import PublishJob, PublishJobStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.provider_connection_repository import (
    ProviderConnectionRepository,
)
from app.services.provider_connection_service import ProviderConnectionService

METRICS_URL = "/api/v1/metrics"
_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")

_EXPECTED_TOP_KEYS = {"publish_jobs", "campaigns", "provider_connections"}
_PUBLISH_KEYS = {"queued", "running", "succeeded", "failed", "total"}
_CAMPAIGN_KEYS = {"active", "archived", "total"}
_CONNECTION_KEYS = {"connected", "disconnected", "total"}


def _seed_campaign(
    db_session: Session,
    tenant: Tenant,
    user: User,
    *,
    name: str,
    status: CampaignStatus = CampaignStatus.DRAFT,
) -> Campaign:
    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name=name,
        status=status,
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


def test_metrics_requires_authentication(client: TestClient) -> None:
    response = client.get(METRICS_URL)
    assert response.status_code == 401


def test_metrics_empty_tenant(
    client: TestClient, auth_fixture
) -> None:
    _user, token = auth_fixture()
    response = client.get(
        METRICS_URL, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == _EXPECTED_TOP_KEYS
    assert body["publish_jobs"] == {
        "queued": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "total": 0,
    }
    assert body["campaigns"] == {"active": 0, "archived": 0, "total": 0}
    assert body["provider_connections"] == {
        "connected": 0,
        "disconnected": 0,
        "total": 0,
    }


def test_metrics_populated_endpoint(
    client: TestClient,
    db_session: Session,
    auth_fixture,
) -> None:
    user, token = auth_fixture()
    tenant = db_session.get(Tenant, user.tenant_id)
    assert tenant is not None

    draft = _seed_campaign(db_session, tenant, user, name="API Draft")
    _seed_campaign(
        db_session,
        tenant,
        user,
        name="API Archived",
        status=CampaignStatus.ARCHIVED,
    )
    other = _seed_campaign(db_session, tenant, user, name="API Other")
    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=draft.id,
            status=PublishJobStatus.QUEUED,
            attempt_count=0,
        )
    )
    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=other.id,
            status=PublishJobStatus.FAILED,
            attempt_count=1,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            error_message="safe truncated message",
        )
    )
    db_session.flush()

    ProviderConnectionService(
        ProviderConnectionRepository(db_session),
        CredentialEncryptionService(_TEST_ENCRYPTION_KEY),
        db_session,
    ).connect(
        tenant_id=tenant.id,
        provider=Provider.META,
        credential_payload=b"api-secret-must-not-leak",
    )

    response = client.get(
        METRICS_URL, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["publish_jobs"]["queued"] == 1
    assert body["publish_jobs"]["failed"] == 1
    assert body["publish_jobs"]["total"] == 2
    assert body["campaigns"]["active"] == 2
    assert body["campaigns"]["archived"] == 1
    assert body["campaigns"]["total"] == 3
    assert body["provider_connections"]["connected"] == 1
    assert body["provider_connections"]["total"] == 1


def test_metrics_response_schema(client: TestClient, auth_fixture) -> None:
    _user, token = auth_fixture()
    body = client.get(
        METRICS_URL, headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert set(body.keys()) == _EXPECTED_TOP_KEYS
    assert set(body["publish_jobs"].keys()) == _PUBLISH_KEYS
    assert set(body["campaigns"].keys()) == _CAMPAIGN_KEYS
    assert set(body["provider_connections"].keys()) == _CONNECTION_KEYS
    for section in body.values():
        for value in section.values():
            assert isinstance(value, int)
            assert value >= 0


def test_metrics_no_secret_leakage(
    client: TestClient, db_session: Session, auth_fixture
) -> None:
    user, token = auth_fixture()
    ProviderConnectionService(
        ProviderConnectionRepository(db_session),
        CredentialEncryptionService(_TEST_ENCRYPTION_KEY),
        db_session,
    ).connect(
        tenant_id=user.tenant_id,
        provider=Provider.GOOGLE,
        credential_payload=b"super-secret-token-value",
    )
    response = client.get(
        METRICS_URL, headers={"Authorization": f"Bearer {token}"}
    )
    text = response.text
    assert response.status_code == 200
    assert "super-secret-token-value" not in text
    assert "encrypted_credentials" not in text
    assert "credential_payload" not in text
    assert "DATABASE_URL" not in text
    assert "postgresql+" not in text
    assert "jwt_secret" not in text.lower()
    assert "Bearer " not in text
