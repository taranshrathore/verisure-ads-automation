"""Service-level tests for CampaignService.

Constructs CampaignRepository/CampaignService directly against db_session
(the same savepoint-isolated session used by API tests), independent of
the HTTP layer.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    CampaignNotFoundError,
    CampaignValidationError,
    InvalidCampaignStateError,
)
from app.models.campaign import CampaignBudgetType, CampaignObjective, CampaignStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.campaign_repository import CampaignRepository
from app.services.campaign_service import CampaignService


@pytest.fixture
def campaign_service(db_session: Session) -> CampaignService:
    return CampaignService(CampaignRepository(db_session), db_session)


def _make_tenant_and_user(db_session: Session, *, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"Campaign Svc Tenant {suffix}", slug=f"campaign-svc-{suffix}"
    )
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"campaign-svc-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    return tenant, user


def test_create_draft_requires_only_name(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="a")

    campaign = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="My Draft"
    )

    assert campaign.status == CampaignStatus.DRAFT
    assert campaign.objective is None
    assert campaign.budget_amount is None


def test_get_campaign_returns_tenant_scoped_campaign(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="b")
    created = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="X"
    )

    fetched = campaign_service.get_campaign(tenant_id=tenant.id, campaign_id=created.id)

    assert fetched.id == created.id


def test_list_campaigns_is_tenant_scoped(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant_a, user_a = _make_tenant_and_user(db_session, suffix="c1")
    tenant_b, user_b = _make_tenant_and_user(db_session, suffix="c2")
    campaign_service.create_draft(
        tenant_id=tenant_a.id, created_by_user_id=user_a.id, name="A1"
    )
    campaign_service.create_draft(
        tenant_id=tenant_b.id, created_by_user_id=user_b.id, name="B1"
    )

    results = campaign_service.list_campaigns(tenant_id=tenant_a.id, limit=50, offset=0)

    assert len(results) == 1
    assert results[0].name == "A1"


def test_duplicate_names_are_allowed_within_a_tenant(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="d")
    campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="Same Name"
    )
    campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="Same Name"
    )

    results = campaign_service.list_campaigns(tenant_id=tenant.id, limit=50, offset=0)
    assert len(results) == 2


def test_update_draft_merges_partial_changes(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="e")
    campaign = campaign_service.create_draft(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name="Original",
        objective=CampaignObjective.AWARENESS,
    )

    updated = campaign_service.update_draft(
        tenant_id=tenant.id, campaign_id=campaign.id, updates={"name": "Renamed"}
    )

    assert updated.name == "Renamed"
    assert updated.objective == CampaignObjective.AWARENESS  # untouched by the PATCH


def test_update_draft_explicit_null_clears_field(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="f")
    campaign = campaign_service.create_draft(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name="Has objective",
        objective=CampaignObjective.TRAFFIC,
    )

    updated = campaign_service.update_draft(
        tenant_id=tenant.id, campaign_id=campaign.id, updates={"objective": None}
    )

    assert updated.objective is None


def test_update_draft_rejects_invalid_merged_budget(
    campaign_service: CampaignService, db_session: Session
) -> None:
    """Setting only budget_amount (leaving currency/budget_type unset)
    fails merged-triple validation before any write is attempted.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="g")
    campaign = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="X"
    )

    with pytest.raises(CampaignValidationError):
        campaign_service.update_draft(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            updates={"budget_amount": Decimal("10.00")},
        )


def test_archive_draft_succeeds(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="h")
    campaign = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="X"
    )

    archived = campaign_service.archive_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    assert archived.status == CampaignStatus.ARCHIVED


def test_update_after_archive_raises_invalid_state(
    campaign_service: CampaignService, db_session: Session
) -> None:
    """Also validates the conditional-UPDATE race guard itself: the
    predicate re-checks status == draft at write time, not merely at the
    service's earlier read time, so an edit arriving after a (real or
    concurrent) archive is rejected rather than silently applied.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="i")
    campaign = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="X"
    )
    campaign_service.archive_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    with pytest.raises(InvalidCampaignStateError):
        campaign_service.update_draft(
            tenant_id=tenant.id, campaign_id=campaign.id, updates={"name": "Y"}
        )


def test_archive_twice_raises_invalid_state(
    campaign_service: CampaignService, db_session: Session
) -> None:
    """Exercises the same conditional-UPDATE guard on the archive path."""
    tenant, user = _make_tenant_and_user(db_session, suffix="j")
    campaign = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="X"
    )
    campaign_service.archive_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    with pytest.raises(InvalidCampaignStateError):
        campaign_service.archive_campaign(tenant_id=tenant.id, campaign_id=campaign.id)


def test_cross_tenant_get_raises_not_found(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant_a, user_a = _make_tenant_and_user(db_session, suffix="k1")
    tenant_b, _ = _make_tenant_and_user(db_session, suffix="k2")
    campaign = campaign_service.create_draft(
        tenant_id=tenant_a.id, created_by_user_id=user_a.id, name="X"
    )

    with pytest.raises(CampaignNotFoundError):
        campaign_service.get_campaign(tenant_id=tenant_b.id, campaign_id=campaign.id)


def test_cross_tenant_update_raises_not_found(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant_a, user_a = _make_tenant_and_user(db_session, suffix="l1")
    tenant_b, _ = _make_tenant_and_user(db_session, suffix="l2")
    campaign = campaign_service.create_draft(
        tenant_id=tenant_a.id, created_by_user_id=user_a.id, name="X"
    )

    with pytest.raises(CampaignNotFoundError):
        campaign_service.update_draft(
            tenant_id=tenant_b.id, campaign_id=campaign.id, updates={"name": "Y"}
        )


def test_cross_tenant_archive_raises_not_found(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant_a, user_a = _make_tenant_and_user(db_session, suffix="m1")
    tenant_b, _ = _make_tenant_and_user(db_session, suffix="m2")
    campaign = campaign_service.create_draft(
        tenant_id=tenant_a.id, created_by_user_id=user_a.id, name="X"
    )

    with pytest.raises(CampaignNotFoundError):
        campaign_service.archive_campaign(tenant_id=tenant_b.id, campaign_id=campaign.id)


def test_archived_campaign_remains_retrievable(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="n")
    campaign = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="X"
    )
    campaign_service.archive_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    fetched = campaign_service.get_campaign(tenant_id=tenant.id, campaign_id=campaign.id)
    assert fetched.status == CampaignStatus.ARCHIVED


def test_archived_campaign_appears_in_default_list(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="o")
    campaign = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="X"
    )
    campaign_service.archive_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    results = campaign_service.list_campaigns(tenant_id=tenant.id, limit=50, offset=0)
    assert any(c.id == campaign.id for c in results)


def test_status_filtered_list_returns_only_matching_status(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="p")
    draft = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="Draft one"
    )
    to_archive = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="To archive"
    )
    campaign_service.archive_campaign(tenant_id=tenant.id, campaign_id=to_archive.id)

    drafts = campaign_service.list_campaigns(
        tenant_id=tenant.id, limit=50, offset=0, status=CampaignStatus.DRAFT
    )
    archived = campaign_service.list_campaigns(
        tenant_id=tenant.id, limit=50, offset=0, status=CampaignStatus.ARCHIVED
    )

    assert [c.id for c in drafts] == [draft.id]
    assert [c.id for c in archived] == [to_archive.id]


def test_update_draft_rejects_unknown_field(
    campaign_service: CampaignService, db_session: Session
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="q")
    campaign = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="X"
    )

    with pytest.raises(CampaignValidationError):
        campaign_service.update_draft(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            updates={"status": CampaignStatus.ACTIVE},
        )


def test_create_draft_rejects_excess_decimal_precision(
    campaign_service: CampaignService, db_session: Session
) -> None:
    """PostgreSQL's NUMERIC(12, 2) rounds a 3-decimal-place amount instead
    of erroring (verified: 10.995 silently becomes 11.00). This must be
    rejected before any write, not silently rounded.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="r")

    with pytest.raises(CampaignValidationError):
        campaign_service.create_draft(
            tenant_id=tenant.id,
            created_by_user_id=user.id,
            name="X",
            budget_type=CampaignBudgetType.DAILY,
            budget_amount=Decimal("10.995"),
            currency="USD",
        )


def test_create_draft_rejects_budget_amount_exceeding_numeric_column_range(
    campaign_service: CampaignService, db_session: Session
) -> None:
    """A value outside NUMERIC(12, 2)'s range would raise an unhandled
    DB-level overflow error; it must be rejected with a clean 422 instead.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="s")

    with pytest.raises(CampaignValidationError):
        campaign_service.create_draft(
            tenant_id=tenant.id,
            created_by_user_id=user.id,
            name="X",
            budget_type=CampaignBudgetType.DAILY,
            budget_amount=Decimal("99999999999.99"),
            currency="USD",
        )


def test_update_draft_rejects_naive_datetime_mixed_with_aware(
    campaign_service: CampaignService, db_session: Session
) -> None:
    """A naive start_at compared against an aware end_at (or vice versa)
    previously raised an unhandled TypeError instead of a 422.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="t")
    campaign = campaign_service.create_draft(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name="X",
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(CampaignValidationError):
        campaign_service.update_draft(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            updates={"end_at": datetime(2026, 1, 2)},  # naive, no tzinfo
        )


def test_list_pagination_is_deterministic_when_created_at_ties(
    campaign_service: CampaignService, db_session: Session
) -> None:
    """Two campaigns created in the same transaction share an identical
    created_at (PostgreSQL now() is transaction-start time). Without an
    id tie-breaker in ORDER BY, LIMIT/OFFSET pagination over such ties is
    unordered and can skip or repeat a row across pages.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="u")
    first = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="First"
    )
    second = campaign_service.create_draft(
        tenant_id=tenant.id, created_by_user_id=user.id, name="Second"
    )
    assert first.created_at == second.created_at  # sanity: the tie is real

    page_1 = campaign_service.list_campaigns(tenant_id=tenant.id, limit=1, offset=0)
    page_2 = campaign_service.list_campaigns(tenant_id=tenant.id, limit=1, offset=1)

    seen_ids = [page_1[0].id, page_2[0].id]
    assert sorted(seen_ids) == sorted([first.id, second.id])
    assert seen_ids[0] != seen_ids[1]
