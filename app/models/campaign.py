"""Campaign model: the core, provider-neutral aggregate for campaign management.

MILESTONE 1 SCOPE: only ``draft`` and ``archived`` are reachable through
CampaignService/the campaigns API today. The remaining CampaignStatus
members (``ready``, ``publishing``, ``active``, ``paused``, ``completed``,
``failed``) are declared now so that adding them later never requires an
``ALTER TYPE ... ADD VALUE`` migration (awkward and, in older PostgreSQL
versions, transaction-unsafe) -- but nothing in this codebase can set a
campaign to one of them yet. objective/budget/schedule fields are all
nullable because a draft campaign is allowed to be genuinely incomplete;
CampaignService enforces the budget-triple/positivity/currency/schedule
invariants (mirrored below as database CHECK constraints) before any
future readiness transition is introduced.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class CampaignObjective(StrEnum):
    """Provider-neutral campaign objective."""

    AWARENESS = "awareness"
    TRAFFIC = "traffic"
    LEADS = "leads"
    CONVERSIONS = "conversions"


class CampaignBudgetType(StrEnum):
    """How a campaign's budget amount is allocated over time."""

    DAILY = "daily"
    LIFETIME = "lifetime"


class CampaignStatus(StrEnum):
    """Campaign lifecycle state.

    Only DRAFT and ARCHIVED are reachable in Milestone 1 -- see the module
    docstring for why the remaining members exist in the enum already.
    """

    DRAFT = "draft"
    READY = "ready"
    PUBLISHING = "publishing"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Return an enum's member *values* for use as values_callable.

    Without this, SQLAlchemy's Enum type persists a PEP-435 enum member's
    ``.name`` (e.g. "AWARENESS") rather than its ``.value`` ("awareness"),
    which would not match the lowercase labels the migration creates on
    the PostgreSQL enum type.
    """
    return [member.value for member in enum_cls]


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A tenant-owned advertising campaign, provider-neutral by design.

    No relationship() to Tenant/User is declared here (Milestone 1
    deliberately omits it): tenant_id participates in two separate FK
    constraints (the plain tenant FK below, and the composite creator FK
    in __table_args__), which would make an ORM relationship's join
    condition ambiguous without extra foreign_keys=/overlaps= wiring that
    isn't needed yet. Callers use the plain UUID columns directly.

    Archiving sets status=ARCHIVED; it never sets deleted_at. deleted_at
    is reserved for true soft-deletion (not used by any code path yet) --
    an archived campaign remains a fully retrievable, listable row.
    """

    __tablename__ = "campaigns"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_campaigns_created_by_user_id_tenant_id_users",
        ),
        # Required so CampaignDeployment can hold a composite FK
        # (campaign_id, tenant_id) -> campaigns(id, tenant_id), the same
        # structural pattern used for the creator FK above. id alone is
        # already the primary key, but PostgreSQL still requires an
        # explicit unique constraint on the exact (id, tenant_id) column
        # pair for a composite FK to reference it.
        UniqueConstraint("id", "tenant_id", name="uq_campaigns_id_tenant_id"),
        CheckConstraint(
            "(budget_type IS NULL AND budget_amount IS NULL AND currency IS NULL) "
            "OR (budget_type IS NOT NULL AND budget_amount IS NOT NULL AND currency IS NOT NULL)",
            name="ck_campaigns_budget_fields_all_or_none",
        ),
        CheckConstraint(
            "budget_amount IS NULL OR budget_amount > 0",
            name="ck_campaigns_budget_amount_positive",
        ),
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="ck_campaigns_currency_iso4217",
        ),
        CheckConstraint(
            "start_at IS NULL OR end_at IS NULL OR end_at > start_at",
            name="ck_campaigns_schedule_order",
        ),
        Index(
            "ix_campaigns_tenant_id_status_active",
            "tenant_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_campaigns_tenant_id_tenants"),
        nullable=False,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[CampaignObjective | None] = mapped_column(
        Enum(
            CampaignObjective,
            name="campaign_objective",
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    budget_type: Mapped[CampaignBudgetType | None] = mapped_column(
        Enum(
            CampaignBudgetType,
            name="campaign_budget_type",
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    budget_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(
            CampaignStatus,
            name="campaign_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        server_default=text("'draft'::campaign_status"),
    )
