"""Service-level tests for CampaignDeploymentService.

Constructs CampaignDeploymentRepository/CampaignDeploymentService directly
against db_session (the same savepoint-isolated session used by API
tests), independent of the HTTP layer. No API exists for deployments yet.
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    CampaignDeploymentNotFoundError,
    InvalidCampaignDeploymentStateError,
)
from app.core.providers import Provider
from app.models.campaign import Campaign
from app.models.campaign_deployment import CampaignDeployment, CampaignDeploymentStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)
from app.services.campaign_deployment_service import CampaignDeploymentService


@pytest.fixture
def deployment_repository(db_session: Session) -> CampaignDeploymentRepository:
    return CampaignDeploymentRepository(db_session)


@pytest.fixture
def deployment_service(
    deployment_repository: CampaignDeploymentRepository, db_session: Session
) -> CampaignDeploymentService:
    return CampaignDeploymentService(deployment_repository, db_session)


def _make_tenant_and_user(db_session: Session, *, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"Deployment Svc Tenant {suffix}", slug=f"deployment-svc-{suffix}"
    )
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"deployment-svc-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    return tenant, user


def _make_campaign(db_session: Session, tenant: Tenant, user: User) -> Campaign:
    campaign = Campaign(
        tenant_id=tenant.id, created_by_user_id=user.id, name="Deployment target"
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


# --- Happy-path transitions ------------------------------------------------


def test_create_pending_deployment_sets_initial_state(
    deployment_service: CampaignDeploymentService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="a")
    campaign = _make_campaign(db_session, tenant, user)

    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
    )

    assert deployment.status == CampaignDeploymentStatus.PENDING
    assert deployment.idempotency_key
    assert deployment.external_campaign_id is None
    assert deployment.submitted_at is None
    assert deployment.confirmed_at is None


def test_full_happy_path_pending_to_submitted_to_live_to_paused_to_live(
    deployment_service: CampaignDeploymentService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="b")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
    )

    submitted = deployment_service.mark_submitted(
        tenant_id=tenant.id,
        deployment_id=deployment.id,
        external_campaign_id="ext-123",
    )
    assert submitted.status == CampaignDeploymentStatus.SUBMITTED
    assert submitted.external_campaign_id == "ext-123"
    assert submitted.submitted_at is not None

    live = deployment_service.mark_live(tenant_id=tenant.id, deployment_id=deployment.id)
    assert live.status == CampaignDeploymentStatus.LIVE
    assert live.confirmed_at is not None

    paused = deployment_service.mark_paused(
        tenant_id=tenant.id, deployment_id=deployment.id
    )
    assert paused.status == CampaignDeploymentStatus.PAUSED

    live_again = deployment_service.mark_live(
        tenant_id=tenant.id, deployment_id=deployment.id
    )
    assert live_again.status == CampaignDeploymentStatus.LIVE


def test_mark_failed_from_submitted(
    deployment_service: CampaignDeploymentService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="c")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.GOOGLE,
    )
    deployment_service.mark_submitted(
        tenant_id=tenant.id, deployment_id=deployment.id, external_campaign_id="ext-1"
    )

    failed = deployment_service.mark_failed(
        tenant_id=tenant.id,
        deployment_id=deployment.id,
        last_error_message="provider rejected the request",
    )

    assert failed.status == CampaignDeploymentStatus.FAILED
    assert failed.last_error_message == "provider rejected the request"


def test_mark_failed_from_pending(
    deployment_service: CampaignDeploymentService, db_session: Session
) -> None:
    """pending -> failed (added in Milestone 5): a deployment can fail
    before ever being submitted, e.g. when PublishCampaignService's
    adapter dispatch declines or raises immediately.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="i")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
    )

    failed = deployment_service.mark_failed(
        tenant_id=tenant.id,
        deployment_id=deployment.id,
        last_error_message="adapter declined before submission",
    )

    assert failed.status == CampaignDeploymentStatus.FAILED
    assert failed.last_error_message == "adapter declined before submission"


# --- Invalid transitions ----------------------------------------------------


def test_mark_live_from_pending_raises_invalid_state(
    deployment_service: CampaignDeploymentService, db_session: Session
) -> None:
    """pending -> live is not in the allowed-transitions table."""
    tenant, user = _make_tenant_and_user(db_session, suffix="d")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
    )

    with pytest.raises(InvalidCampaignDeploymentStateError):
        deployment_service.mark_live(tenant_id=tenant.id, deployment_id=deployment.id)


def test_mark_submitted_twice_raises_invalid_state(
    deployment_service: CampaignDeploymentService, db_session: Session
) -> None:
    """submitted -> submitted is not an allowed transition (FAILED-like
    terminality of the pending edge: it may only be taken once).
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="e")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
    )
    deployment_service.mark_submitted(
        tenant_id=tenant.id, deployment_id=deployment.id, external_campaign_id="ext-1"
    )

    with pytest.raises(InvalidCampaignDeploymentStateError):
        deployment_service.mark_submitted(
            tenant_id=tenant.id,
            deployment_id=deployment.id,
            external_campaign_id="ext-2",
        )


def test_mark_paused_from_failed_raises_invalid_state(
    deployment_service: CampaignDeploymentService, db_session: Session
) -> None:
    """FAILED is terminal: no outgoing transition is allowed from it."""
    tenant, user = _make_tenant_and_user(db_session, suffix="f")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
    )
    deployment_service.mark_submitted(
        tenant_id=tenant.id, deployment_id=deployment.id, external_campaign_id="ext-1"
    )
    deployment_service.mark_failed(
        tenant_id=tenant.id, deployment_id=deployment.id, last_error_message="boom"
    )

    with pytest.raises(InvalidCampaignDeploymentStateError):
        deployment_service.mark_paused(tenant_id=tenant.id, deployment_id=deployment.id)


def test_cross_tenant_mark_submitted_raises_not_found(
    deployment_service: CampaignDeploymentService, db_session: Session
) -> None:
    tenant_a, user_a = _make_tenant_and_user(db_session, suffix="g1")
    tenant_b, _ = _make_tenant_and_user(db_session, suffix="g2")
    campaign = _make_campaign(db_session, tenant_a, user_a)
    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant_a.id,
        campaign_id=campaign.id,
        provider=Provider.META,
    )

    with pytest.raises(CampaignDeploymentNotFoundError):
        deployment_service.mark_submitted(
            tenant_id=tenant_b.id,
            deployment_id=deployment.id,
            external_campaign_id="ext-1",
        )


# --- Concurrency / TOCTOU regression ----------------------------------------


def test_stale_transition_is_rejected_by_conditional_update(
    deployment_service: CampaignDeploymentService,
    deployment_repository: CampaignDeploymentRepository,
    db_session: Session,
) -> None:
    """Reproduces the exact race the optimistic conditional update
    closes: the service's initial lookup returns a stale snapshot (as if
    a concurrent transaction had already committed a transition after
    this read started), but the write-time WHERE status =
    expected_current_status clause in
    CampaignDeploymentRepository.update_status still catches the
    mismatch, and the service surfaces that as
    InvalidCampaignDeploymentStateError instead of silently re-applying
    (and re-committing side effects for) a transition that already
    happened.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="h")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
    )
    deployment_service.mark_submitted(
        tenant_id=tenant.id, deployment_id=deployment.id, external_campaign_id="ext-1"
    )
    # Real, committed status is now SUBMITTED. Force the service's next
    # lookup to return a stale snapshot that still says PENDING, as if
    # this read had started before the mark_submitted above committed.
    stale_snapshot = CampaignDeployment(
        id=deployment.id,
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
        idempotency_key=str(uuid.uuid4()),
        status=CampaignDeploymentStatus.PENDING,
    )
    original_get_by_id = deployment_repository.get_by_id
    deployment_repository.get_by_id = lambda tenant_id, deployment_id: stale_snapshot

    try:
        # The stale snapshot says PENDING, so the service's Python-level
        # check (PENDING -> SUBMITTED is allowed) passes -- it is only
        # the repository's conditional UPDATE, re-checking the real
        # database row, that must reject this.
        with pytest.raises(InvalidCampaignDeploymentStateError):
            deployment_service.mark_submitted(
                tenant_id=tenant.id,
                deployment_id=deployment.id,
                external_campaign_id="ext-2",
            )
    finally:
        deployment_repository.get_by_id = original_get_by_id

    # The real row is untouched by the rejected attempt.
    db_session.refresh(deployment)
    assert deployment.status == CampaignDeploymentStatus.SUBMITTED
    assert deployment.external_campaign_id == "ext-1"


# --- Transaction/session safety on persistence failure (Milestone 5 audit) -----


def test_session_remains_usable_after_repository_failure_during_mark_submitted(
    deployment_service: CampaignDeploymentService,
    deployment_repository: CampaignDeploymentRepository,
    db_session: Session,
) -> None:
    """mark_submitted performs two writes before committing: the
    transition UPDATE (inside _apply_transition) and
    update_external_reference. Before the Milestone 5 audit fix, a
    failure in the second write propagated without rolling back the
    first, leaving it uncommitted-but-applied within the still-open
    transaction -- so a subsequent read on the very same session would
    see the half-applied SUBMITTED status. This proves that failure
    now rolls back cleanly: the status reverts to PENDING and the
    session can still run further statements afterward.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="k")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
    )

    original_update_external_reference = deployment_repository.update_external_reference

    def _raise_simulated_db_failure(*args: object, **kwargs: object) -> int:
        raise RuntimeError("simulated database failure")

    deployment_repository.update_external_reference = _raise_simulated_db_failure  # type: ignore[method-assign]

    try:
        with pytest.raises(RuntimeError, match="simulated database failure"):
            deployment_service.mark_submitted(
                tenant_id=tenant.id,
                deployment_id=deployment.id,
                external_campaign_id="ext-1",
            )
    finally:
        deployment_repository.update_external_reference = (
            original_update_external_reference
        )

    # The session must still be usable -- no PendingRollbackError -- and
    # the half-applied transition status UPDATE must have been rolled
    # back rather than left sitting uncommitted.
    reloaded = deployment_repository.get_by_id(tenant.id, deployment.id)
    assert reloaded is not None
    assert reloaded.status == CampaignDeploymentStatus.PENDING
    assert reloaded.external_campaign_id is None


def test_session_remains_usable_after_repository_failure_during_create(
    deployment_service: CampaignDeploymentService,
    deployment_repository: CampaignDeploymentRepository,
    db_session: Session,
) -> None:
    """Same guarantee as above, exercised at create_pending_deployment: a
    commit failure must roll back and leave the session usable for the
    next call rather than requiring the caller to recover manually.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="l")
    campaign = _make_campaign(db_session, tenant, user)
    # Commit tenant/campaign as their own checkpoint first -- db_session
    # uses a SAVEPOINT per commit/rollback (see conftest.py), so without
    # this, the rollback below (correctly) discards *everything* since
    # the last checkpoint, which would otherwise include this setup too
    # and is not what this test is exercising.
    db_session.commit()

    original_create = deployment_repository.create

    def _raise_simulated_db_failure(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated database failure")

    deployment_repository.create = _raise_simulated_db_failure  # type: ignore[method-assign]

    try:
        with pytest.raises(RuntimeError, match="simulated database failure"):
            deployment_service.create_pending_deployment(
                tenant_id=tenant.id,
                campaign_id=campaign.id,
                provider=Provider.META,
            )
    finally:
        deployment_repository.create = original_create

    # The session must still be usable for a completely unrelated call.
    deployment = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.GOOGLE,
    )
    assert deployment.status == CampaignDeploymentStatus.PENDING
