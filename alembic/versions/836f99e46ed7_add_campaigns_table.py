"""add campaigns table

Revision ID: 836f99e46ed7
Revises: c4d8f1a9b6e3
Create Date: 2026-08-01 13:27:28.386785

Adds the Campaign Management Milestone 1 schema:
- Three PostgreSQL enum types (campaign_objective, campaign_budget_type,
  campaign_status), created explicitly here and referenced from the
  `campaigns` table with create_type=False so the type is never created
  twice.
- The `campaigns` table itself, with a plain FK to tenants and a
  composite FK (created_by_user_id, tenant_id) -> users(id, tenant_id)
  that makes cross-tenant campaign creatorship structurally impossible.
- Restores `uq_users_id_tenant_id` on `users` (dropped by c4d8f1a9b6e3
  when the local RBAC engine that was its only prior consumer was
  removed) -- it now has a genuine consumer again: the composite FK
  above.
- One partial index, `ix_campaigns_tenant_id_status_active`, supporting
  the tenant-scoped "active" (non-soft-deleted) campaign list/filter
  queries CampaignRepository issues.

Does not touch tenants, users' other columns, or refresh_tokens.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '836f99e46ed7'
down_revision: Union[str, Sequence[str], None] = 'c4d8f1a9b6e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


campaign_objective_enum = postgresql.ENUM(
    "awareness",
    "traffic",
    "leads",
    "conversions",
    name="campaign_objective",
)
campaign_budget_type_enum = postgresql.ENUM(
    "daily",
    "lifetime",
    name="campaign_budget_type",
)
campaign_status_enum = postgresql.ENUM(
    "draft",
    "ready",
    "publishing",
    "active",
    "paused",
    "completed",
    "failed",
    "archived",
    name="campaign_status",
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    campaign_objective_enum.create(bind, checkfirst=True)
    campaign_budget_type_enum.create(bind, checkfirst=True)
    campaign_status_enum.create(bind, checkfirst=True)

    # Restored: had exactly one prior consumer (user_role_assignments'
    # composite FK), removed along with the local RBAC engine in
    # c4d8f1a9b6e3. campaigns' composite creator FK is a new, independent
    # consumer.
    op.create_unique_constraint("uq_users_id_tenant_id", "users", ["id", "tenant_id"])

    op.create_table(
        "campaigns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "objective",
            postgresql.ENUM(
                "awareness",
                "traffic",
                "leads",
                "conversions",
                name="campaign_objective",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "budget_type",
            postgresql.ENUM(
                "daily",
                "lifetime",
                name="campaign_budget_type",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("budget_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft",
                "ready",
                "publishing",
                "active",
                "paused",
                "completed",
                "failed",
                "archived",
                name="campaign_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'draft'::campaign_status"),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_campaigns_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_campaigns_created_by_user_id_tenant_id_users",
        ),
        sa.CheckConstraint(
            "(budget_type IS NULL AND budget_amount IS NULL AND currency IS NULL) "
            "OR (budget_type IS NOT NULL AND budget_amount IS NOT NULL AND currency IS NOT NULL)",
            name="ck_campaigns_budget_fields_all_or_none",
        ),
        sa.CheckConstraint(
            "budget_amount IS NULL OR budget_amount > 0",
            name="ck_campaigns_budget_amount_positive",
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="ck_campaigns_currency_iso4217",
        ),
        sa.CheckConstraint(
            "start_at IS NULL OR end_at IS NULL OR end_at > start_at",
            name="ck_campaigns_schedule_order",
        ),
    )
    op.create_index(
        "ix_campaigns_tenant_id_status_active",
        "campaigns",
        ["tenant_id", "status"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_campaigns_tenant_id_status_active",
        table_name="campaigns",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("campaigns")
    op.drop_constraint("uq_users_id_tenant_id", "users", type_="unique")

    bind = op.get_bind()
    campaign_status_enum.drop(bind, checkfirst=True)
    campaign_budget_type_enum.drop(bind, checkfirst=True)
    campaign_objective_enum.drop(bind, checkfirst=True)
