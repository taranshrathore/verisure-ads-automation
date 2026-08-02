"""add provider connections table

Revision ID: c49243d65a23
Revises: 0d7ba5dd4102
Create Date: 2026-08-02 11:37:56.946316

Adds the Provider Account Connection + Encrypted Credential Storage
milestone's Phase 2 schema (model only -- see
app/models/provider_connection.py; no repository/service/API/OAuth
exists yet):

- Two PostgreSQL enum types (provider_connection_provider,
  provider_connection_status), created explicitly here and referenced
  from the `provider_connections` table with create_type=False so the
  type is never created twice.
- The `provider_connections` table itself: one row per (tenant_id,
  provider) even after disconnect (UNIQUE(tenant_id, provider) is never
  dropped on disconnect -- a future reconnect must reactivate this same
  row rather than insert a new one), plus a plain FK to tenants.
- ck_provider_connections_status_credentials_coherent: a connected row
  must carry non-NULL encrypted_credentials and a NULL disconnected_at;
  a disconnected row must have encrypted_credentials cleared to NULL and
  a non-NULL disconnected_at. This makes it structurally impossible to
  retain usable ciphertext on a disconnected row, independent of
  whatever a future service layer does.
- No additional index beyond UNIQUE(tenant_id, provider): that
  constraint's own index already covers tenant-scoped lookups (its
  leading column is tenant_id), so a separate index would be redundant.

Does not touch tenants, users, campaigns, or campaign_deployments.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c49243d65a23'
down_revision: Union[str, Sequence[str], None] = '0d7ba5dd4102'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


provider_connection_provider_enum = postgresql.ENUM(
    "meta",
    "google",
    name="provider_connection_provider",
)
provider_connection_status_enum = postgresql.ENUM(
    "connected",
    "disconnected",
    name="provider_connection_status",
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    provider_connection_provider_enum.create(bind, checkfirst=True)
    provider_connection_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "provider_connections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "provider",
            postgresql.ENUM(
                "meta",
                "google",
                name="provider_connection_provider",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=True),
        sa.Column("external_account_id", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column(
            "credentials_expire_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "connected",
                "disconnected",
                name="provider_connection_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'connected'::provider_connection_status"),
        ),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_provider_connections_tenant_id_tenants",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            name="uq_provider_connections_tenant_id_provider",
        ),
        sa.CheckConstraint(
            "(status = 'connected' AND encrypted_credentials IS NOT NULL "
            "AND disconnected_at IS NULL) OR (status = 'disconnected' "
            "AND encrypted_credentials IS NULL AND disconnected_at IS NOT NULL)",
            name="ck_provider_connections_status_credentials_coherent",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("provider_connections")

    bind = op.get_bind()
    provider_connection_status_enum.drop(bind, checkfirst=True)
    provider_connection_provider_enum.drop(bind, checkfirst=True)
