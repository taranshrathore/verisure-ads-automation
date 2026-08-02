"""add publish jobs table

Revision ID: b7e4a91c2d08
Revises: c49243d65a23
Create Date: 2026-08-02 15:55:00.000000

Adds the Async Publish / Background Worker Foundation Phase 1 schema
(model only -- see app/models/publish_job.py; no repository/service/
worker/API exists yet):

- PostgreSQL enum type publish_job_status (queued, running, succeeded,
  failed), created explicitly here and referenced from the
  `publish_jobs` table with create_type=False so the type is never
  created twice.
- The `publish_jobs` table: tenant FK, composite campaign FK
  (campaign_id, tenant_id) -> campaigns(id, tenant_id), optional
  requested_by_user_id FK to users, timezone-aware started_at/
  finished_at, error_message VARCHAR(2000), attempt_count with
  non-negative CHECK.
- Partial unique index uq_publish_jobs_campaign_id_active: at most one
  queued or running job per campaign.
- Worker polling index ix_publish_jobs_status_created_at_id on
  (status, created_at, id).

Does not touch tenants, users, campaigns, campaign_deployments, or
provider_connections.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b7e4a91c2d08"
down_revision: Union[str, Sequence[str], None] = "c49243d65a23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


publish_job_status_enum = postgresql.ENUM(
    "queued",
    "running",
    "succeeded",
    "failed",
    name="publish_job_status",
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    publish_job_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "publish_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "queued",
                "running",
                "succeeded",
                "failed",
                name="publish_job_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'queued'::publish_job_status"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_publish_jobs_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "tenant_id"],
            ["campaigns.id", "campaigns.tenant_id"],
            name="fk_publish_jobs_campaign_id_tenant_id_campaigns",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_publish_jobs_requested_by_user_id_users",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_publish_jobs_attempt_count_non_negative",
        ),
    )
    op.create_index(
        "uq_publish_jobs_campaign_id_active",
        "publish_jobs",
        ["campaign_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_publish_jobs_status_created_at_id",
        "publish_jobs",
        ["status", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_publish_jobs_status_created_at_id",
        table_name="publish_jobs",
    )
    op.drop_index(
        "uq_publish_jobs_campaign_id_active",
        table_name="publish_jobs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_table("publish_jobs")

    bind = op.get_bind()
    publish_job_status_enum.drop(bind, checkfirst=True)
