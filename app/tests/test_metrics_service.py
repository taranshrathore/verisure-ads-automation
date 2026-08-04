"""Unit tests for MetricsService (aggregate counts; read-only)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet
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
from app.services.metrics_service import MetricsService
from app.services.provider_connection_service import ProviderConnectionService

_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")

_EMPTY = {
    "publish_jobs": {
        "queued": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "total": 0,
    },
    "campaigns": {"active": 0, "archived": 0, "total": 0},
    "provider_connections": {"connected": 0, "disconnected": 0, "total": 0},
}


def _tenant_user(db_session: Session, *, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(name=f"Metrics Tenant {suffix}", slug=f"metrics-{suffix}")
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"metrics-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    return tenant, user


def _campaign(
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


def _job(
    db_session: Session,
    tenant: Tenant,
    campaign: Campaign,
    *,
    status: PublishJobStatus,
) -> PublishJob:
    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=None,
        status=status,
        attempt_count=0,
        started_at=(
            datetime.now(timezone.utc)
            if status != PublishJobStatus.QUEUED
            else None
        ),
        finished_at=(
            datetime.now(timezone.utc)
            if status
            in {PublishJobStatus.SUCCEEDED, PublishJobStatus.FAILED}
            else None
        ),
    )
    db_session.add(job)
    db_session.flush()
    return job


def _connections(db_session: Session) -> ProviderConnectionService:
    return ProviderConnectionService(
        ProviderConnectionRepository(db_session),
        CredentialEncryptionService(_TEST_ENCRYPTION_KEY),
        db_session,
    )


class _TrackingSession:
    """Delegates to a real Session but records commit/rollback calls."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1
        self._session.commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        self._session.rollback()

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._session.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def test_empty_tenant_returns_zeros(db_session: Session) -> None:
    tenant, _user = _tenant_user(db_session, suffix="empty")
    metrics = MetricsService(db_session).get_metrics(tenant_id=tenant.id)
    assert metrics == _EMPTY


def test_populated_counts(
    db_session: Session,
) -> None:
    tenant, user = _tenant_user(db_session, suffix="pop")
    draft = _campaign(db_session, tenant, user, name="Draft A")
    _campaign(
        db_session,
        tenant,
        user,
        name="Archived A",
        status=CampaignStatus.ARCHIVED,
    )
    # One active (queued) job requires a dedicated campaign due to the
    # partial unique index on active jobs per campaign.
    running_campaign = _campaign(db_session, tenant, user, name="For running")
    succeeded_campaign = _campaign(db_session, tenant, user, name="For ok")
    failed_campaign = _campaign(db_session, tenant, user, name="For fail")
    _job(db_session, tenant, draft, status=PublishJobStatus.QUEUED)
    _job(db_session, tenant, running_campaign, status=PublishJobStatus.RUNNING)
    _job(db_session, tenant, succeeded_campaign, status=PublishJobStatus.SUCCEEDED)
    _job(db_session, tenant, failed_campaign, status=PublishJobStatus.FAILED)

    connections = _connections(db_session)
    connections.connect(
        tenant_id=tenant.id,
        provider=Provider.META,
        credential_payload=b"secret-should-never-appear-in-metrics",
    )
    google = connections.connect(
        tenant_id=tenant.id,
        provider=Provider.GOOGLE,
        credential_payload=b"another-secret-payload",
    )
    connections.disconnect(tenant_id=tenant.id, connection_id=google.id)

    metrics = MetricsService(db_session).get_metrics(tenant_id=tenant.id)
    assert metrics["publish_jobs"] == {
        "queued": 1,
        "running": 1,
        "succeeded": 1,
        "failed": 1,
        "total": 4,
    }
    assert metrics["campaigns"] == {
        "active": 4,  # draft + 3 job campaigns; archived excluded
        "archived": 1,
        "total": 5,
    }
    assert metrics["provider_connections"] == {
        "connected": 1,
        "disconnected": 1,
        "total": 2,
    }


def test_publish_job_counts_only(db_session: Session) -> None:
    tenant, user = _tenant_user(db_session, suffix="jobs")
    c1 = _campaign(db_session, tenant, user, name="J1")
    c2 = _campaign(db_session, tenant, user, name="J2")
    _job(db_session, tenant, c1, status=PublishJobStatus.QUEUED)
    _job(db_session, tenant, c2, status=PublishJobStatus.QUEUED)
    metrics = MetricsService(db_session).get_metrics(tenant_id=tenant.id)
    assert metrics["publish_jobs"]["queued"] == 2
    assert metrics["publish_jobs"]["total"] == 2


def test_campaign_counts_only(db_session: Session) -> None:
    tenant, user = _tenant_user(db_session, suffix="camps")
    _campaign(db_session, tenant, user, name="D1")
    _campaign(db_session, tenant, user, name="D2")
    _campaign(
        db_session, tenant, user, name="A1", status=CampaignStatus.ARCHIVED
    )
    metrics = MetricsService(db_session).get_metrics(tenant_id=tenant.id)
    assert metrics["campaigns"] == {"active": 2, "archived": 1, "total": 3}


def test_provider_connection_counts_only(db_session: Session) -> None:
    tenant, _user = _tenant_user(db_session, suffix="conns")
    connections = _connections(db_session)
    connections.connect(
        tenant_id=tenant.id,
        provider=Provider.META,
        credential_payload=b"opaque-a",
    )
    metrics = MetricsService(db_session).get_metrics(tenant_id=tenant.id)
    assert metrics["provider_connections"] == {
        "connected": 1,
        "disconnected": 0,
        "total": 1,
    }


def test_tenant_isolation(db_session: Session) -> None:
    tenant_a, user_a = _tenant_user(db_session, suffix="iso-a")
    tenant_b, user_b = _tenant_user(db_session, suffix="iso-b")
    _campaign(db_session, tenant_a, user_a, name="Only A")
    _campaign(db_session, tenant_b, user_b, name="Only B")
    _campaign(
        db_session,
        tenant_b,
        user_b,
        name="Archived B",
        status=CampaignStatus.ARCHIVED,
    )
    metrics_a = MetricsService(db_session).get_metrics(tenant_id=tenant_a.id)
    metrics_b = MetricsService(db_session).get_metrics(tenant_id=tenant_b.id)
    assert metrics_a["campaigns"] == {"active": 1, "archived": 0, "total": 1}
    assert metrics_b["campaigns"] == {"active": 1, "archived": 1, "total": 2}


def test_read_only_never_commits_or_rolls_back(db_session: Session) -> None:
    tenant, _user = _tenant_user(db_session, suffix="ro")
    tracking = _TrackingSession(db_session)
    MetricsService(tracking).get_metrics(tenant_id=tenant.id)  # type: ignore[arg-type]
    assert tracking.commits == 0
    assert tracking.rollbacks == 0


def test_unknown_tenant_is_empty(db_session: Session) -> None:
    metrics = MetricsService(db_session).get_metrics(tenant_id=uuid4())
    assert metrics == _EMPTY


def test_metrics_payload_contains_no_secret_material(
    db_session: Session,
) -> None:
    tenant, _user = _tenant_user(db_session, suffix="sec")
    secret = b"credential-secret-must-not-leak"
    _connections(db_session).connect(
        tenant_id=tenant.id,
        provider=Provider.META,
        credential_payload=secret,
    )
    rendered = str(MetricsService(db_session).get_metrics(tenant_id=tenant.id))
    assert "credential-secret-must-not-leak" not in rendered
    assert "encrypted_credentials" not in rendered
    assert "DATABASE_URL" not in rendered
    assert "postgresql" not in rendered.lower()
