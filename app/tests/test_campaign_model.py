"""Database-level constraint tests for the campaigns table.

These exercise real PostgreSQL CHECK/FK constraints directly via the ORM,
independent of CampaignService (which mirrors the same rules in Python
for friendlier 422s -- see test_campaign_service.py for those).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignBudgetType, CampaignObjective, CampaignStatus
from app.models.tenant import Tenant
from app.models.user import User


def _make_tenant_and_user(db_session: Session, *, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"Campaign Model Tenant {suffix}", slug=f"campaign-model-{suffix}"
    )
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"campaign-model-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    return tenant, user


def test_cross_tenant_creator_is_rejected_by_composite_fk(db_session: Session) -> None:
    """A campaign cannot claim a creator who belongs to a different tenant --
    enforced structurally by the composite FK, not just application logic.
    """
    tenant, _ = _make_tenant_and_user(db_session, suffix="a")
    _, other_user = _make_tenant_and_user(db_session, suffix="b")

    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=other_user.id,
        name="Cross-tenant campaign",
    )
    db_session.add(campaign)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_budget_fields_must_be_all_or_none(db_session: Session) -> None:
    """Setting only one of budget_type/budget_amount/currency violates the
    all-or-none CHECK constraint.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="c")

    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name="Partial budget",
        budget_amount=Decimal("100.00"),
    )
    db_session.add(campaign)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_budget_amount_must_be_positive(db_session: Session) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="d")

    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name="Negative budget",
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("-1.00"),
        currency="USD",
    )
    db_session.add(campaign)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_currency_must_be_three_uppercase_letters(db_session: Session) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="e")

    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name="Bad currency",
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("10.00"),
        currency="usd",
    )
    db_session.add(campaign)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_schedule_end_must_be_after_start(db_session: Session) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="f")
    now = datetime.now(timezone.utc)

    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name="Bad schedule",
        start_at=now,
        end_at=now - timedelta(days=1),
    )
    db_session.add(campaign)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_valid_complete_campaign_persists_successfully(db_session: Session) -> None:
    """A fully-specified campaign satisfies every constraint and the
    status column's server-side default resolves to draft.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="g")
    now = datetime.now(timezone.utc)

    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name="Valid campaign",
        objective=CampaignObjective.AWARENESS,
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("50.00"),
        currency="USD",
        start_at=now,
        end_at=now + timedelta(days=7),
    )
    db_session.add(campaign)
    db_session.flush()

    assert campaign.status == CampaignStatus.DRAFT


def test_minimal_draft_with_only_name_persists_successfully(db_session: Session) -> None:
    """A genuinely incomplete draft (name only) is valid -- objective,
    budget, and schedule are all nullable.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="h")

    campaign = Campaign(
        tenant_id=tenant.id, created_by_user_id=user.id, name="Bare minimum draft"
    )
    db_session.add(campaign)
    db_session.flush()

    assert campaign.status == CampaignStatus.DRAFT
    assert campaign.objective is None
    assert campaign.budget_type is None
    assert campaign.budget_amount is None
    assert campaign.currency is None
