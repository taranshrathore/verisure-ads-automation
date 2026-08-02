"""ProviderConnection persistence repository."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.providers import Provider
from app.models.provider_connection import ProviderConnection, ProviderConnectionStatus


class ProviderConnectionRepository:
    """Data-access helpers for ProviderConnection rows. Does not commit or roll back.

    Every method is tenant-scoped: there is no method here that can look
    up or mutate a connection without a tenant_id predicate, and a
    missing row is indistinguishable from a cross-tenant one (both
    return None / affect zero rows), matching the rest of this codebase's
    security pattern. encrypted_credentials is only ever read/written as
    opaque bytes here -- this repository has no encryption/decryption
    logic and never returns credential material through any other means.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, connection: ProviderConnection) -> None:
        """Stage a new provider connection for persistence."""
        self._session.add(connection)

    def get_by_id(
        self, tenant_id: UUID, connection_id: UUID
    ) -> ProviderConnection | None:
        """Return a tenant-scoped connection by ID, or None."""
        return self._session.scalar(
            select(ProviderConnection).where(
                ProviderConnection.id == connection_id,
                ProviderConnection.tenant_id == tenant_id,
            )
        )

    def get_by_provider(
        self, tenant_id: UUID, provider: Provider
    ) -> ProviderConnection | None:
        """Return the tenant-scoped connection for one provider, or None.

        Returned regardless of status (connected or disconnected) --
        uq_provider_connections_tenant_id_provider guarantees at most one
        row per (tenant_id, provider) ever exists.
        """
        return self._session.scalar(
            select(ProviderConnection).where(
                ProviderConnection.tenant_id == tenant_id,
                ProviderConnection.provider == provider,
            )
        )

    def get_connected_by_provider(
        self, tenant_id: UUID, provider: Provider
    ) -> ProviderConnection | None:
        """Return the tenant-scoped, currently usable connection for one provider, or None.

        Requires status == CONNECTED and encrypted_credentials IS NOT
        NULL. The ciphertext predicate is redundant with
        ck_provider_connections_status_credentials_coherent (a connected
        row can never actually have NULL ciphertext -- see
        app/models/provider_connection.py), but is written explicitly
        here as defense-in-depth: this query's correctness then does not
        silently depend on that CHECK constraint continuing to exist.
        """
        return self._session.scalar(
            select(ProviderConnection).where(
                ProviderConnection.tenant_id == tenant_id,
                ProviderConnection.provider == provider,
                ProviderConnection.status == ProviderConnectionStatus.CONNECTED,
                ProviderConnection.encrypted_credentials.is_not(None),
            )
        )

    def list_by_tenant(self, tenant_id: UUID) -> list[ProviderConnection]:
        """Return every connection for one tenant, ordered by provider then id.

        Not paginated: uq_provider_connections_tenant_id_provider bounds
        this to at most one row per Provider member. Ordering by the
        provider column sorts by that PostgreSQL enum type's declared
        label order (meta, google -- see the migration), not
        alphabetically; id is the tie-breaker, though in practice ties
        cannot occur since provider is already unique per tenant.
        """
        stmt = (
            select(ProviderConnection)
            .where(ProviderConnection.tenant_id == tenant_id)
            .order_by(ProviderConnection.provider.asc(), ProviderConnection.id.asc())
        )
        return list(self._session.scalars(stmt))

    def disconnect(
        self, tenant_id: UUID, connection_id: UUID, disconnected_at: datetime
    ) -> int:
        """Atomically disconnect a connected row, destroying its ciphertext.

        A single conditional UPDATE -- WHERE tenant_id/id match AND
        status == CONNECTED -- rather than a load-then-mutate round trip,
        so a concurrent disconnect can never race with this write: only
        the first caller's UPDATE can still match the CONNECTED
        predicate. Returns the affected row count so the caller can
        distinguish "already disconnected, missing, or cross-tenant" (0
        rows) from success (1 row) without a second query.
        """
        # updated_at must be set explicitly: TimestampMixin.onupdate is an
        # ORM-level hook and does not fire for Core UPDATE statements, so
        # omitting it here would leave the public updated_at column stale
        # after a successful disconnect. Use the same wall-clock value as
        # disconnected_at (not func.now()), which is transaction-stable in
        # PostgreSQL and would not advance within a long-lived transaction.
        result = self._session.execute(
            update(ProviderConnection)
            .where(
                ProviderConnection.id == connection_id,
                ProviderConnection.tenant_id == tenant_id,
                ProviderConnection.status == ProviderConnectionStatus.CONNECTED,
            )
            .values(
                status=ProviderConnectionStatus.DISCONNECTED,
                disconnected_at=disconnected_at,
                encrypted_credentials=None,
                updated_at=disconnected_at,
            )
        )
        return result.rowcount
