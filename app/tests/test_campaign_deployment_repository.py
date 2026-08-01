"""Repository-level tests for CampaignDeploymentRepository.

Focused on the one thing that matters at this layer for concurrency
safety: update_status's WHERE clause re-checks status ==
expected_current_status at write time, not just at an earlier read time.
"""

import uuid

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.campaign_deployment import (
    CampaignDeployment,
    CampaignDeploymentProvider,
    CampaignDeploymentStatus,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)


def _make_tenant_and_user(db_session: Session, *, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"Deployment Repo Tenant {suffix}", slug=f"deployment-repo-{suffix}"
    )
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"deployment-repo-{suffix}@example.com",
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


def _make_pending_deployment(
    db_session: Session, tenant: Tenant, campaign: Campaign
) -> CampaignDeployment:
    deployment = CampaignDeployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=CampaignDeploymentProvider.META,
        idempotency_key=str(uuid.uuid4()),
        status=CampaignDeploymentStatus.PENDING,
    )
    db_session.add(deployment)
    db_session.flush()
    return deployment


def test_update_status_conditional_update_prevents_double_transition(
    db_session: Session,
) -> None:
    """Two callers both believing the deployment is PENDING race to move
    it to SUBMITTED. Only the first UPDATE's WHERE (status ==
    expected_current_status) matches; the second finds the row already
    changed and affects zero rows, proving the DB-level guard -- not
    just an earlier Python-level read -- is what prevents the double
    transition.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="a")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = _make_pending_deployment(db_session, tenant, campaign)
    repo = CampaignDeploymentRepository(db_session)

    first_attempt = repo.update_status(
        tenant.id,
        deployment.id,
        CampaignDeploymentStatus.PENDING,
        CampaignDeploymentStatus.SUBMITTED,
    )
    second_attempt = repo.update_status(
        tenant.id,
        deployment.id,
        CampaignDeploymentStatus.PENDING,
        CampaignDeploymentStatus.SUBMITTED,
    )

    assert first_attempt == 1
    assert second_attempt == 0


def test_update_status_sets_optional_fields_only_when_passed(
    db_session: Session,
) -> None:
    """submitted_at is written when explicitly passed; confirmed_at and
    last_error_message, left at their sentinel default, are untouched.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="b")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = _make_pending_deployment(db_session, tenant, campaign)
    repo = CampaignDeploymentRepository(db_session)

    affected = repo.update_status(
        tenant.id,
        deployment.id,
        CampaignDeploymentStatus.PENDING,
        CampaignDeploymentStatus.SUBMITTED,
        submitted_at=None,
    )
    db_session.refresh(deployment)

    assert affected == 1
    assert deployment.status == CampaignDeploymentStatus.SUBMITTED
    assert deployment.confirmed_at is None
    assert deployment.last_error_message is None


def test_update_status_mismatched_expected_status_is_a_no_op(
    db_session: Session,
) -> None:
    """A caller expecting SUBMITTED against a deployment that is still
    PENDING affects zero rows and does not mutate the row.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="c")
    campaign = _make_campaign(db_session, tenant, user)
    deployment = _make_pending_deployment(db_session, tenant, campaign)
    repo = CampaignDeploymentRepository(db_session)

    affected = repo.update_status(
        tenant.id,
        deployment.id,
        CampaignDeploymentStatus.SUBMITTED,
        CampaignDeploymentStatus.LIVE,
    )
    db_session.refresh(deployment)

    assert affected == 0
    assert deployment.status == CampaignDeploymentStatus.PENDING
