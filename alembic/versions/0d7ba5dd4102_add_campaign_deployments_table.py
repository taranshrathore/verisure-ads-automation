"""add campaign deployments table

Revision ID: 0d7ba5dd4102
Revises: 836f99e46ed7
Create Date: 2026-08-01 15:54:33.150137

Adds the Campaign Management Milestone 2 Phase 1 schema:
- Two PostgreSQL enum types (campaign_deployment_provider,
  campaign_deployment_status), created explicitly here and referenced
  from the `campaign_deployments` table with create_type=False so the
  type is never created twice.
- `uq_campaigns_id_tenant_id` on `campaigns`: campaign_deployments needs
  a composite FK (campaign_id, tenant_id) -> campaigns(id, tenant_id),
  the same structural pattern campaigns' own composite creator FK uses
  against users(id, tenant_id). campaigns.id alone is already the
  primary key, but PostgreSQL still requires an explicit unique
  constraint on the exact (id, tenant_id) column pair for a composite FK
  to reference it.
- The `campaign_deployments` table itself, with a plain FK to tenants,
  the composite FK described above, and UNIQUE(campaign_id, provider)
  so a campaign can have at most one deployment per provider.
- Two indexes: (tenant_id, status) for tenant-scoped status filtering,
  and idempotency_key for future publish-retry lookups.

Does not touch tenants, users, refresh_tokens, or any existing campaigns
column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0d7ba5dd4102'
down_revision: Union[str, Sequence[str], None] = '836f99e46ed7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


campaign_deployment_provider_enum = postgresql.ENUM(
    "meta",
    "google",
    name="campaign_deployment_provider",
)
campaign_deployment_status_enum = postgresql.ENUM(
    "pending",
    "submitted",
    "live",
    "paused",
    "failed",
    name="campaign_deployment_status",
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    campaign_deployment_provider_enum.create(bind, checkfirst=True)
    campaign_deployment_status_enum.create(bind, checkfirst=True)

    # New consumer: campaign_deployments' composite campaign FK below.
    op.create_unique_constraint(
        "uq_campaigns_id_tenant_id", "campaigns", ["id", "tenant_id"]
    )

    op.create_table(
        "campaign_deployments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column(
            "provider",
            postgresql.ENUM(
                "meta",
                "google",
                name="campaign_deployment_provider",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("external_campaign_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.String(length=2000), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "submitted",
                "live",
                "paused",
                "failed",
                name="campaign_deployment_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'pending'::campaign_deployment_status"),
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
            name="fk_campaign_deployments_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "tenant_id"],
            ["campaigns.id", "campaigns.tenant_id"],
            name="fk_campaign_deployments_campaign_id_tenant_id_campaigns",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "provider",
            name="uq_campaign_deployments_campaign_id_provider",
        ),
    )
    op.create_index(
        "ix_campaign_deployments_tenant_id_status",
        "campaign_deployments",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_deployments_idempotency_key",
        "campaign_deployments",
        ["idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_campaign_deployments_idempotency_key",
        table_name="campaign_deployments",
    )
    op.drop_index(
        "ix_campaign_deployments_tenant_id_status",
        table_name="campaign_deployments",
    )
    op.drop_table("campaign_deployments")
    op.drop_constraint("uq_campaigns_id_tenant_id", "campaigns", type_="unique")

    bind = op.get_bind()
    campaign_deployment_status_enum.drop(bind, checkfirst=True)
    campaign_deployment_provider_enum.drop(bind, checkfirst=True)
