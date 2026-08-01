"""Service-level tests for PublishCampaignService.

Constructs the real repositories/services directly against db_session,
exactly like test_campaign_deployment_service.py. Uses the real
ProviderAdapterRegistry (i.e. real MetaAdapter/GoogleAdapter, which
still raise NotImplementedError -- see app/adapters/): no network, no
HTTP, no OAuth. See test_publish_campaign_adapter_integration.py for
adapter-dispatch behavior tested against fake adapters instead.
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.adapters.registry import ProviderAdapterRegistry
from app.core.exceptions import CampaignNotFoundError, InvalidCampaignStateError
from app.models.campaign import Campaign, CampaignStatus
from app.models.campaign_deployment import (
    CampaignDeploymentProvider,
    CampaignDeploymentStatus,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)
from app.repositories.campaign_repository import CampaignRepository
from app.services.campaign_deployment_service import CampaignDeploymentService
from app.services.campaign_spec_builder import CampaignSpecBuilder
from app.services.publish_campaign_service import PublishCampaignService


@pytest.fixture
def deployment_repository(db_session: Session) -> CampaignDeploymentRepository:
    return CampaignDeploymentRepository(db_session)


@pytest.fixture
def deployment_service(
    deployment_repository: CampaignDeploymentRepository, db_session: Session
) -> CampaignDeploymentService:
    return CampaignDeploymentService(deployment_repository, db_session)


@pytest.fixture
def publish_service(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    db_session: Session,
) -> PublishCampaignService:
    return PublishCampaignService(
        CampaignRepository(db_session),
        deployment_repository,
        deployment_service,
        CampaignSpecBuilder(),
        ProviderAdapterRegistry(),
        db_session,
    )


def _make_tenant_and_user(db_session: Session, *, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"Publish Svc Tenant {suffix}", slug=f"publish-svc-{suffix}"
    )
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"publish-svc-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    return tenant, user


def _make_campaign(
    db_session: Session, tenant: Tenant, user: User, **overrides: object
) -> Campaign:
    defaults: dict[str, object] = dict(
        tenant_id=tenant.id, created_by_user_id=user.id, name="Publish target"
    )
    defaults.update(overrides)
    campaign = Campaign(**defaults)
    db_session.add(campaign)
    db_session.flush()
    return campaign


# --- Negative paths ----------------------------------------------------------


def test_publish_raises_when_campaign_missing(
    publish_service: PublishCampaignService, db_session: Session
) -> None:
    tenant, _ = _make_tenant_and_user(db_session, suffix="a")

    with pytest.raises(CampaignNotFoundError):
        publish_service.publish_campaign(
            tenant_id=tenant.id, campaign_id=uuid.uuid4()
        )


def test_publish_rejects_archived_campaign(
    publish_service: PublishCampaignService, db_session: Session
) -> None:
    """Covers both "archived campaign rejected" and "invalid campaign":
    the only publishability check this orchestration performs is the
    archived-state rejection -- campaign completeness is
    CampaignSpecBuilder's job, and publish_campaign does not call it in
    this milestone (see module docstring).
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="b")
    campaign = _make_campaign(
        db_session, tenant, user, status=CampaignStatus.ARCHIVED
    )

    with pytest.raises(InvalidCampaignStateError):
        publish_service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)


def test_publish_cross_tenant_campaign_raises_not_found(
    publish_service: PublishCampaignService, db_session: Session
) -> None:
    tenant_a, user_a = _make_tenant_and_user(db_session, suffix="c1")
    tenant_b, _ = _make_tenant_and_user(db_session, suffix="c2")
    campaign = _make_campaign(db_session, tenant_a, user_a)

    with pytest.raises(CampaignNotFoundError):
        publish_service.publish_campaign(
            tenant_id=tenant_b.id, campaign_id=campaign.id
        )


# --- Deployment creation -----------------------------------------------------


def test_publish_creates_meta_deployment(
    publish_service: PublishCampaignService, db_session: Session
) -> None:
    """The deployment row is created for META either way; its final status
    is FAILED (not PENDING) because MetaAdapter.publish is a real adapter
    that still raises NotImplementedError -- see app/adapters/meta_adapter.py
    -- and PublishCampaignService turns that into a mark_failed transition
    rather than letting it escape.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="d")
    campaign = _make_campaign(db_session, tenant, user)

    deployments = publish_service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )

    meta = next(
        d for d in deployments if d.provider == CampaignDeploymentProvider.META
    )
    assert meta.status == CampaignDeploymentStatus.FAILED
    assert meta.last_error_message is not None
    assert meta.campaign_id == campaign.id
    assert meta.tenant_id == tenant.id


def test_publish_creates_google_deployment(
    publish_service: PublishCampaignService, db_session: Session
) -> None:
    """See test_publish_creates_meta_deployment: GoogleAdapter.publish also
    still raises NotImplementedError, so the deployment ends up FAILED.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="e")
    campaign = _make_campaign(db_session, tenant, user)

    deployments = publish_service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )

    google = next(
        d for d in deployments if d.provider == CampaignDeploymentProvider.GOOGLE
    )
    assert google.status == CampaignDeploymentStatus.FAILED
    assert google.last_error_message is not None
    assert google.campaign_id == campaign.id
    assert google.tenant_id == tenant.id


def test_publish_creates_exactly_one_deployment_per_supported_provider(
    publish_service: PublishCampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="f")
    campaign = _make_campaign(db_session, tenant, user)

    deployments = publish_service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )

    providers = {d.provider for d in deployments}
    assert providers == {
        CampaignDeploymentProvider.META,
        CampaignDeploymentProvider.GOOGLE,
    }
    assert len(deployments) == 2


# --- Idempotency across repeated publish calls ------------------------------


def test_second_publish_does_not_duplicate_deployments(
    publish_service: PublishCampaignService,
    deployment_repository: CampaignDeploymentRepository,
    db_session: Session,
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="g")
    campaign = _make_campaign(db_session, tenant, user)

    first_call = publish_service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )
    second_call = publish_service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )

    assert {d.id for d in first_call} == {d.id for d in second_call}

    all_deployments = deployment_repository.list_by_campaign(tenant.id, campaign.id)
    assert len(all_deployments) == 2


def test_existing_deployment_is_reused_not_recreated_or_reset(
    publish_service: PublishCampaignService,
    deployment_service: CampaignDeploymentService,
    db_session: Session,
) -> None:
    """A deployment already advanced past PENDING (e.g. submitted) must
    come back unchanged from publish_campaign, not be silently reset.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="h")
    campaign = _make_campaign(db_session, tenant, user)

    pre_existing = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=CampaignDeploymentProvider.META,
    )
    submitted = deployment_service.mark_submitted(
        tenant_id=tenant.id,
        deployment_id=pre_existing.id,
        external_campaign_id="already-submitted-ext-id",
    )

    deployments = publish_service.publish_campaign(
        tenant_id=tenant.id, campaign_id=campaign.id
    )

    meta = next(
        d for d in deployments if d.provider == CampaignDeploymentProvider.META
    )
    assert meta.id == submitted.id
    assert meta.status == CampaignDeploymentStatus.SUBMITTED
    assert meta.external_campaign_id == "already-submitted-ext-id"
