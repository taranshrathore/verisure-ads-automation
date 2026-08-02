"""Provider-neutral campaign specification.

Pure domain layer: plain, frozen dataclasses only. No ORM, no
SQLAlchemy, no Pydantic, no I/O. This is the shape a future provider
adapter (Meta, Google) will consume to submit a campaign -- adapters
translate a CampaignSpec into provider-specific API calls; they never
need to see a Campaign/CampaignDeployment row directly.

CampaignObjective and CampaignBudgetType are reused from
app.models.campaign rather than duplicated here: they are already
plain, provider-neutral StrEnums with no SQLAlchemy coupling of their
own, and a second parallel enum would just be a drift risk with no
benefit. Provider is reused from app.core.providers for the same
reason -- it is the one canonical provider enum for the whole project.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.core.providers import Provider
from app.models.campaign import CampaignBudgetType, CampaignObjective


@dataclass(frozen=True)
class CampaignBudget:
    """Provider-neutral budget: allocation type, amount, and currency."""

    type: CampaignBudgetType
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class CampaignSchedule:
    """Provider-neutral campaign run window."""

    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class CampaignSpec:
    """A complete, provider-neutral description of one campaign deployment.

    Deliberately contains only concepts every provider understands in
    some form; it has no knowledge of Meta/Google-specific fields, IDs,
    or API shapes. Built by CampaignSpecBuilder from a Campaign plus one
    of its CampaignDeployment rows -- never constructed directly from
    request/API input.
    """

    campaign_id: UUID
    tenant_id: UUID
    provider: Provider
    objective: CampaignObjective
    budget: CampaignBudget
    schedule: CampaignSchedule
