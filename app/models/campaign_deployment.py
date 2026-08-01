"""CampaignDeployment model: a campaign's per-provider deployment record.

MILESTONE 2 PHASE 1 SCOPE: this is a model + migration only. No repository,
service, adapter, publishing logic, or API exists yet -- rows are not
created or mutated by any code path in this codebase today. The shape
exists now so later phases (publish orchestration, provider adapters) have
a stable, provider-neutral place to record external campaign identity and
submission state without altering the Campaign aggregate itself.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CampaignDeploymentProvider(StrEnum):
    """Ad platform a campaign is deployed to."""

    META = "meta"
    GOOGLE = "google"


class CampaignDeploymentStatus(StrEnum):
    """Lifecycle state of a single provider deployment attempt."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    LIVE = "live"
    PAUSED = "paused"
    FAILED = "failed"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Return an enum's member *values* for use as values_callable.

    Without this, SQLAlchemy's Enum type persists a PEP-435 enum member's
    ``.name`` (e.g. "META") rather than its ``.value`` ("meta"), which
    would not match the lowercase labels the migration creates on the
    PostgreSQL enum type.
    """
    return [member.value for member in enum_cls]


class CampaignDeployment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A campaign's deployment record for one specific ad provider.

    No relationship() to Campaign/Tenant is declared here, for the same
    reason Campaign omits one for User/Tenant: tenant_id participates in
    two separate FK constraints (the plain tenant FK below, and the
    composite campaign FK in __table_args__), which would make an ORM
    relationship's join condition ambiguous without extra foreign_keys=/
    overlaps= wiring that isn't needed yet. Callers use the plain UUID
    columns directly.
    """

    __tablename__ = "campaign_deployments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "tenant_id"],
            ["campaigns.id", "campaigns.tenant_id"],
            name="fk_campaign_deployments_campaign_id_tenant_id_campaigns",
        ),
        UniqueConstraint(
            "campaign_id",
            "provider",
            name="uq_campaign_deployments_campaign_id_provider",
        ),
        Index(
            "ix_campaign_deployments_tenant_id_status",
            "tenant_id",
            "status",
        ),
        Index(
            "ix_campaign_deployments_idempotency_key",
            "idempotency_key",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_campaign_deployments_tenant_id_tenants"),
        nullable=False,
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider: Mapped[CampaignDeploymentProvider] = mapped_column(
        Enum(
            CampaignDeploymentProvider,
            name="campaign_deployment_provider",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    external_campaign_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[CampaignDeploymentStatus] = mapped_column(
        Enum(
            CampaignDeploymentStatus,
            name="campaign_deployment_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        server_default=text("'pending'::campaign_deployment_status"),
    )
