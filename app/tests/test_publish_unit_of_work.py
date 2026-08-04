"""Unit-of-work / nested-commit hardening tests for async publish.

Verifies PublishJobService.run_once is the sole committer for the worker
path, while standalone CampaignDeploymentService / PublishCampaignService
calls still commit by default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.adapters.base_adapter import BaseAdapter
from app.adapters.models import PublishResult
from app.core.campaign_spec import CampaignSpec
from app.core.provider_credentials import ProviderCredentials
from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService
from app.models.campaign import (
    Campaign,
    CampaignBudgetType,
    CampaignObjective,
    CampaignStatus,
)
from app.models.campaign_deployment import (
    CampaignDeployment,
    CampaignDeploymentStatus,
)
from app.models.publish_job import PublishJob, PublishJobStatus
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


class _TrackingSession:
    """Delegates to a real Session while counting commit/rollback calls."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.commits = 0
        self.rollbacks = 0
        self.begin_nested_calls = 0

    def commit(self) -> None:
        self.commits += 1
        self._session.commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        self._session.rollback()

    def begin_nested(self) -> Any:
        self.begin_nested_calls += 1
        return self._session.begin_nested()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


class _FakeSuccessAdapter(BaseAdapter):
    def __init__(self, external_id: str) -> None:
        self._external_id = external_id

    def publish(
        self, spec: CampaignSpec, credentials: ProviderCredentials
    ) -> PublishResult:
        del spec, credentials
        return PublishResult(
            success=True,
            external_campaign_id=self._external_id,
            error_message=None,
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


def _tenant_user_campaign(
    db_session: Session, *, suffix: str
) -> tuple[Tenant, User, Campaign]:
    tenant = Tenant(
        name=f"UoW Tenant {suffix}",
        slug=f"uow-{suffix}-{uuid4().hex[:6]}",
    )
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"uow-{suffix}-{uuid4().hex[:6]}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name=f"UoW campaign {suffix}",
        objective=CampaignObjective.CONVERSIONS,
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("50.00"),
        currency="USD",
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        status=CampaignStatus.DRAFT,
    )
    db_session.add(campaign)
    db_session.flush()
    return tenant, user, campaign


def _connect_providers(
    db_session: Session, tenant_id
) -> ProviderConnectionService:
    connections = ProviderConnectionService(
        ProviderConnectionRepository(db_session),
        CredentialEncryptionService(_TEST_ENCRYPTION_KEY),
        db_session,
    )
    for provider in (Provider.META, Provider.GOOGLE):
        connections.connect(
            tenant_id=tenant_id,
            provider=provider,
            credential_payload=b"opaque-uow-credential",
        )
    return connections


def _build_publish(
    db_session: Session,
    *,
    registry: _FakeRegistry,
    connections: ProviderConnectionService,
    deployment_service: CampaignDeploymentService | None = None,
) -> PublishCampaignService:
    deployment_repository = CampaignDeploymentRepository(db_session)
    return PublishCampaignService(
        CampaignRepository(db_session),
        deployment_repository,
        deployment_service
        or CampaignDeploymentService(deployment_repository, db_session),
        CampaignSpecBuilder(),
        registry,  # type: ignore[arg-type]
        connections,
        db_session,
    )


def test_run_once_successful_publish_commits_once(db_session: Session) -> None:
    tracking = _TrackingSession(db_session)
    tenant, user, campaign = _tenant_user_campaign(
        tracking, suffix="ok"  # type: ignore[arg-type]
    )
    connections = _connect_providers(tracking, tenant.id)  # type: ignore[arg-type]
    registry = _FakeRegistry(
        {
            Provider.META: _FakeSuccessAdapter("meta-1"),
            Provider.GOOGLE: _FakeSuccessAdapter("google-1"),
        }
    )
    publish = _build_publish(
        tracking,  # type: ignore[arg-type]
        registry=registry,
        connections=connections,
    )
    jobs = PublishJobRepository(tracking)  # type: ignore[arg-type]
    service = PublishJobService(
        jobs,
        CampaignRepository(tracking),  # type: ignore[arg-type]
        publish,
        tracking,  # type: ignore[arg-type]
    )
    service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    tracking.commits = 0
    tracking.rollbacks = 0
    tracking.begin_nested_calls = 0
    assert service.run_once() is True
    assert tracking.commits == 1
    assert tracking.rollbacks == 0
    assert tracking.begin_nested_calls == 1

    deployments = CampaignDeploymentRepository(db_session).list_by_campaign(
        tenant.id, campaign.id
    )
    assert len(deployments) == 2
    assert {d.status for d in deployments} == {CampaignDeploymentStatus.SUBMITTED}


def test_run_once_success_marks_job_succeeded(db_session: Session) -> None:
    tenant, user, campaign = _tenant_user_campaign(db_session, suffix="ok2")
    connections = _connect_providers(db_session, tenant.id)
    registry = _FakeRegistry(
        {
            Provider.META: _FakeSuccessAdapter("meta-1"),
            Provider.GOOGLE: _FakeSuccessAdapter("google-1"),
        }
    )
    publish = _build_publish(
        db_session, registry=registry, connections=connections
    )
    jobs = PublishJobRepository(db_session)
    service = PublishJobService(
        jobs, CampaignRepository(db_session), publish, db_session
    )
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    assert service.run_once() is True
    loaded = jobs.get_by_id(tenant.id, job.id)
    assert loaded is not None
    assert loaded.status == PublishJobStatus.SUCCEEDED


def test_escaping_publish_failure_rolls_back_deployment_writes(
    db_session: Session,
) -> None:
    """If recording a provider outcome raises, nested work is rolled back."""
    tenant, user, campaign = _tenant_user_campaign(db_session, suffix="fail-rb")
    connections = _connect_providers(db_session, tenant.id)
    registry = _FakeRegistry(
        {
            Provider.META: _FakeSuccessAdapter("meta-1"),
            Provider.GOOGLE: _FakeSuccessAdapter("google-1"),
        }
    )
    deployment_repository = CampaignDeploymentRepository(db_session)
    real_deployment_service = CampaignDeploymentService(
        deployment_repository, db_session
    )

    class _BoomOnSecondSubmit(CampaignDeploymentService):
        def __init__(self) -> None:
            super().__init__(deployment_repository, db_session)
            self._submits = 0

        def mark_submitted(self, **kwargs):  # type: ignore[no-untyped-def]
            self._submits += 1
            if self._submits >= 2:
                raise RuntimeError("persistence boom while recording submit")
            return real_deployment_service.mark_submitted(**kwargs)

    publish = _build_publish(
        db_session,
        registry=registry,
        connections=connections,
        deployment_service=_BoomOnSecondSubmit(),
    )
    jobs = PublishJobRepository(db_session)
    service = PublishJobService(
        jobs, CampaignRepository(db_session), publish, db_session
    )
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    with pytest.raises(RuntimeError, match="persistence boom"):
        service.run_once()

    loaded = jobs.get_by_id(tenant.id, job.id)
    assert loaded is not None
    assert loaded.status == PublishJobStatus.FAILED
    # Savepoint rolled back deployment creates/submits from the failed attempt.
    deployments = deployment_repository.list_by_campaign(tenant.id, campaign.id)
    assert deployments == []


def test_run_once_passes_commit_false_to_publish(db_session: Session) -> None:
    tenant, user, campaign = _tenant_user_campaign(db_session, suffix="flag")
    seen: dict[str, bool] = {}

    class _RecordingPublish:
        def publish_campaign(self, *, tenant_id, campaign_id, commit: bool = True):
            seen["commit"] = commit
            return []

    service = PublishJobService(
        PublishJobRepository(db_session),
        CampaignRepository(db_session),
        _RecordingPublish(),  # type: ignore[arg-type]
        db_session,
    )
    service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    assert service.run_once() is True
    assert seen.get("commit") is False


def test_standalone_publish_campaign_still_commits(db_session: Session) -> None:
    tracking = _TrackingSession(db_session)
    tenant, _user, campaign = _tenant_user_campaign(
        tracking, suffix="api"  # type: ignore[arg-type]
    )
    connections = _connect_providers(tracking, tenant.id)  # type: ignore[arg-type]
    registry = _FakeRegistry(
        {
            Provider.META: _FakeSuccessAdapter("meta-1"),
            Provider.GOOGLE: _FakeSuccessAdapter("google-1"),
        }
    )
    publish = _build_publish(
        tracking,  # type: ignore[arg-type]
        registry=registry,
        connections=connections,
    )
    tracking.commits = 0
    deployments = publish.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )
    assert len(deployments) == 2
    # Default commit=True: create_pending + mark_submitted per provider.
    assert tracking.commits >= 2
    assert {d.status for d in deployments} == {CampaignDeploymentStatus.SUBMITTED}


def test_deployment_service_commit_false_does_not_commit(
    db_session: Session,
) -> None:
    tracking = _TrackingSession(db_session)
    tenant, _user, campaign = _tenant_user_campaign(
        tracking, suffix="flush"  # type: ignore[arg-type]
    )
    service = CampaignDeploymentService(
        CampaignDeploymentRepository(tracking),  # type: ignore[arg-type]
        tracking,  # type: ignore[arg-type]
    )
    tracking.commits = 0
    deployment = service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
        commit=False,
    )
    assert tracking.commits == 0
    assert deployment.id is not None
    loaded = CampaignDeploymentRepository(tracking).get_by_id(  # type: ignore[arg-type]
        tenant.id, deployment.id
    )
    assert loaded is not None

    tracking.rollback()
    tracking._session.expire_all()
    assert (
        CampaignDeploymentRepository(db_session).get_by_id(tenant.id, deployment.id)
        is None
    )


def test_repositories_still_never_commit(db_session: Session) -> None:
    tracking = _TrackingSession(db_session)
    tenant, _user, campaign = _tenant_user_campaign(
        tracking, suffix="repo"  # type: ignore[arg-type]
    )
    jobs = PublishJobRepository(tracking)  # type: ignore[arg-type]
    deployments = CampaignDeploymentRepository(tracking)  # type: ignore[arg-type]
    tracking.commits = 0

    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        status=PublishJobStatus.QUEUED,
        attempt_count=0,
    )
    jobs.create(job)
    tracking.flush()
    assert tracking.commits == 0

    deployments.create(
        CampaignDeployment(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            provider=Provider.META,
            idempotency_key=str(uuid4()),
            status=CampaignDeploymentStatus.PENDING,
        )
    )
    tracking.flush()
    assert tracking.commits == 0
