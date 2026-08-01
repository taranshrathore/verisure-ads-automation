"""Pure unit tests for CampaignSpecBuilder.

No database, no repository, no HTTP: Campaign/CampaignDeployment rows
are constructed as plain in-memory objects (never added to a session,
so no DB access occurs anywhere in this file).
"""

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.campaign_spec import CampaignBudget, CampaignSchedule, CampaignSpec
from app.core.exceptions import CampaignValidationError
from app.models.campaign import Campaign, CampaignBudgetType, CampaignObjective
from app.models.campaign_deployment import (
    CampaignDeployment,
    CampaignDeploymentProvider,
)
from app.services.campaign_spec_builder import CampaignSpecBuilder


def _make_complete_campaign(**overrides: object) -> Campaign:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        created_by_user_id=uuid.uuid4(),
        name="Complete campaign",
        objective=CampaignObjective.CONVERSIONS,
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("125.50"),
        currency="USD",
        start_at=now,
        end_at=now + timedelta(days=30),
    )
    defaults.update(overrides)
    return Campaign(**defaults)


def _make_deployment(
    campaign: Campaign,
    provider: CampaignDeploymentProvider = CampaignDeploymentProvider.META,
) -> CampaignDeployment:
    return CampaignDeployment(
        id=uuid.uuid4(),
        tenant_id=campaign.tenant_id,
        campaign_id=campaign.id,
        provider=provider,
        idempotency_key=str(uuid.uuid4()),
    )


def test_build_succeeds_for_a_complete_campaign() -> None:
    campaign = _make_complete_campaign()
    deployment = _make_deployment(campaign)

    spec = CampaignSpecBuilder.build(campaign, deployment)

    assert isinstance(spec, CampaignSpec)


def test_build_maps_exact_values() -> None:
    campaign = _make_complete_campaign()
    deployment = _make_deployment(campaign, provider=CampaignDeploymentProvider.GOOGLE)

    spec = CampaignSpecBuilder.build(campaign, deployment)

    assert spec.campaign_id == campaign.id
    assert spec.tenant_id == campaign.tenant_id
    assert spec.provider == CampaignDeploymentProvider.GOOGLE
    assert spec.objective == campaign.objective
    assert spec.budget == CampaignBudget(
        type=campaign.budget_type,
        amount=campaign.budget_amount,
        currency=campaign.currency,
    )
    assert spec.schedule == CampaignSchedule(
        start_at=campaign.start_at, end_at=campaign.end_at
    )


def test_build_output_is_immutable() -> None:
    campaign = _make_complete_campaign()
    deployment = _make_deployment(campaign)

    spec = CampaignSpecBuilder.build(campaign, deployment)

    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.campaign_id = uuid.uuid4()  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.budget.amount = Decimal("1.00")  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.schedule.start_at = datetime.now(timezone.utc)  # type: ignore[misc]


def test_build_raises_when_objective_is_missing() -> None:
    campaign = _make_complete_campaign(objective=None)
    deployment = _make_deployment(campaign)

    with pytest.raises(CampaignValidationError):
        CampaignSpecBuilder.build(campaign, deployment)


def test_build_raises_when_budget_is_missing() -> None:
    campaign = _make_complete_campaign(
        budget_type=None, budget_amount=None, currency=None
    )
    deployment = _make_deployment(campaign)

    with pytest.raises(CampaignValidationError):
        CampaignSpecBuilder.build(campaign, deployment)


def test_build_raises_when_schedule_is_missing() -> None:
    campaign = _make_complete_campaign(start_at=None, end_at=None)
    deployment = _make_deployment(campaign)

    with pytest.raises(CampaignValidationError):
        CampaignSpecBuilder.build(campaign, deployment)
