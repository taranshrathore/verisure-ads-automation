"""PublishJob model: durable async publish work item for one campaign.

ASYNC PUBLISH PHASE 1 SCOPE: model + migration only. No repository,
service, worker, or API exists yet -- rows are not created or mutated by
any code path in this codebase today. The shape exists so later phases
(enqueue, SKIP LOCKED claim, worker execution) have a stable place to
record publish-job lifecycle without altering Campaign or
CampaignDeployment.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class PublishJobStatus(StrEnum):
    """Lifecycle state of a single campaign publish job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Return an enum's member *values* for use as values_callable.

    Without this, SQLAlchemy's Enum type persists a PEP-435 enum member's
    ``.name`` (e.g. "QUEUED") rather than its ``.value`` ("queued"), which
    would not match the lowercase labels the migration creates on the
    PostgreSQL enum type.
    """
    return [member.value for member in enum_cls]


class PublishJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant-scoped async publish job for one campaign.

    No relationship() to Campaign/Tenant/User is declared here, matching
    CampaignDeployment: tenant_id participates in both a plain tenant FK
    and a composite campaign FK, which would make ORM relationship join
    conditions ambiguous without extra wiring that is not needed yet.
    Callers use the plain UUID columns directly.
    """

    __tablename__ = "publish_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "tenant_id"],
            ["campaigns.id", "campaigns.tenant_id"],
            name="fk_publish_jobs_campaign_id_tenant_id_campaigns",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_publish_jobs_attempt_count_non_negative",
        ),
        Index(
            "uq_publish_jobs_campaign_id_active",
            "campaign_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "ix_publish_jobs_status_created_at_id",
            "status",
            "created_at",
            "id",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_publish_jobs_tenant_id_tenants"),
        nullable=False,
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_publish_jobs_requested_by_user_id_users"),
        nullable=True,
    )
    status: Mapped[PublishJobStatus] = mapped_column(
        Enum(
            PublishJobStatus,
            name="publish_job_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        server_default=text("'queued'::publish_job_status"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
