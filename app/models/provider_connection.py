"""ProviderConnection model: a tenant's stored credential for one ad provider.

encrypted_credentials is fully opaque bytes here: this model has no
knowledge of what an encrypted payload contains or which provider
produced it. Encryption/decryption lives in
app/core/security/credential_encryption.py; persistence and lifecycle
mutations live in ProviderConnectionRepository /
ProviderConnectionService. There is still no OAuth connect endpoint --
rows are created via ProviderConnectionService.connect() (service/API
callers only) until a real provider OAuth contract exists.

Lifecycle is intentionally binary (connected/disconnected) and enforced
structurally, not just by application code: ck_provider_connections_status_
credentials_coherent below makes it impossible to persist a connected row
without ciphertext, or a disconnected row that still has ciphertext.
Disconnecting a row clears encrypted_credentials entirely
(ProviderConnectionRepository.disconnect() sets status, disconnected_at,
and encrypted_credentials=NULL atomically) -- the row itself is kept for
audit/history, and UNIQUE(tenant_id, provider) means a future reconnect
must reactivate this same row rather than insert a new one.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.providers import Provider
from app.database.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ProviderConnectionStatus(StrEnum):
    """Lifecycle state of a tenant's connection to one ad provider."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Return an enum's member *values* for use as values_callable.

    Without this, SQLAlchemy's Enum type persists a PEP-435 enum member's
    ``.name`` (e.g. "CONNECTED") rather than its ``.value`` ("connected"),
    which would not match the lowercase labels the migration creates on
    the PostgreSQL enum type.
    """
    return [member.value for member in enum_cls]


class ProviderConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant's encrypted credential connection to one ad provider.

    No relationship() to Tenant is declared here: this model only ever
    needs the plain tenant_id column value, and every campaign-domain
    model in this codebase follows the same plain-UUID-column convention
    rather than declaring ORM relationships it doesn't need yet.
    """

    __tablename__ = "provider_connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            name="uq_provider_connections_tenant_id_provider",
        ),
        CheckConstraint(
            "("
            "status = 'connected' "
            "AND encrypted_credentials IS NOT NULL "
            "AND disconnected_at IS NULL"
            ") OR ("
            "status = 'disconnected' "
            "AND encrypted_credentials IS NULL "
            "AND disconnected_at IS NOT NULL"
            ")",
            name="ck_provider_connections_status_credentials_coherent",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_provider_connections_tenant_id_tenants"),
        nullable=False,
    )
    provider: Mapped[Provider] = mapped_column(
        Enum(
            Provider,
            name="provider_connection_provider",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    encrypted_credentials: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    external_account_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credentials_expire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[ProviderConnectionStatus] = mapped_column(
        Enum(
            ProviderConnectionStatus,
            name="provider_connection_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        server_default=text("'connected'::provider_connection_status"),
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
