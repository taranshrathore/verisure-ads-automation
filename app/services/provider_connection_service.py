"""ProviderConnection lifecycle + credential-access service.

ProviderConnectionService owns all transaction commits;
ProviderConnectionRepository never commits or rolls back. Every method
takes tenant_id explicitly and is tenant-scoped throughout, matching
CampaignDeploymentService's conventions.

Every public method that writes (connect, disconnect) wraps its full
read-validate-write sequence in a single try/except that rolls back on
ANY exception -- a domain exception, an encryption failure, or a
persistence failure alike -- before re-raising. This never leaves a
partially-applied, uncommitted transaction (or a session stuck needing a
rollback it never got) behind for the next caller.

PHASE 4 SCOPE: connect/disconnect/read/decrypt only. No OAuth, no
adapter integration, no provider HTTP calls, no background jobs. connect()
intentionally treats an existing DISCONNECTED row as terminal --
reactivating it (reconnection) is deferred to a future milestone; see
connect()'s docstring.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    InvalidProviderConnectionStateError,
    ProviderConnectionAlreadyExistsError,
    ProviderConnectionNotFoundError,
)
from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService
from app.models.provider_connection import ProviderConnection, ProviderConnectionStatus
from app.repositories.provider_connection_repository import (
    ProviderConnectionRepository,
)

# Matches the named UNIQUE constraint created by migration c49243d65a23.
# Used to translate a concurrent-insert race into the same domain exception
# the pre-insert get_by_provider check would have raised.
_UQ_TENANT_PROVIDER = "uq_provider_connections_tenant_id_provider"


class ProviderConnectionService:
    """Orchestrates ProviderConnection lifecycle transitions and decrypted-
    credential access. No FastAPI dependency, no global settings access:
    both collaborators (repository, encryption service) are injected.
    """

    def __init__(
        self,
        connection_repository: ProviderConnectionRepository,
        encryption_service: CredentialEncryptionService,
        session: Session,
    ) -> None:
        self._connections = connection_repository
        self._encryption = encryption_service
        self._session = session

    def connect(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: Provider,
        credential_payload: bytes,
        external_account_id: str | None = None,
        display_name: str | None = None,
        credentials_expire_at: datetime | None = None,
    ) -> ProviderConnection:
        """Create a new CONNECTED row for one (tenant, provider) pair.

        If a row already exists for this pair (uq_provider_connections_
        tenant_id_provider guarantees at most one):

        - CONNECTED: raises ProviderConnectionAlreadyExistsError.
        - DISCONNECTED: raises InvalidProviderConnectionStateError. This
          milestone intentionally keeps disconnected terminal -- a future
          reconnection milestone will reactivate this same existing row
          rather than change this behavior.

        credential_payload is encrypted before the row is ever
        constructed, so no plaintext is ever staged for persistence.
        """
        try:
            existing = self._connections.get_by_provider(tenant_id, provider)
            if existing is not None:
                if existing.status == ProviderConnectionStatus.CONNECTED:
                    raise ProviderConnectionAlreadyExistsError()
                raise InvalidProviderConnectionStateError(
                    "This provider was previously disconnected; "
                    "reconnection is not supported yet."
                )

            encrypted_credentials = self._encryption.encrypt_credentials(
                tenant_id, provider, credential_payload
            )
            connection = ProviderConnection(
                tenant_id=tenant_id,
                provider=provider,
                status=ProviderConnectionStatus.CONNECTED,
                encrypted_credentials=encrypted_credentials,
                external_account_id=external_account_id,
                display_name=display_name,
                credentials_expire_at=credentials_expire_at,
            )
            self._connections.create(connection)
            self._session.commit()
        except IntegrityError as exc:
            # TOCTOU: a concurrent connect can insert between get_by_provider
            # and this commit. The UNIQUE constraint is the real guard; map
            # that specific violation to the same domain exception the
            # earlier status check would have raised, so callers never see a
            # raw IntegrityError/500 for a known duplicate.
            self._session.rollback()
            if _UQ_TENANT_PROVIDER in str(getattr(exc, "orig", exc)):
                raise ProviderConnectionAlreadyExistsError() from exc
            raise
        except Exception:
            self._session.rollback()
            raise
        return connection

    def disconnect(
        self, *, tenant_id: uuid.UUID, connection_id: uuid.UUID
    ) -> ProviderConnection:
        """Disconnect a CONNECTED row, destroying its ciphertext.

        Missing/cross-tenant raises ProviderConnectionNotFoundError.
        Attempting to disconnect a row that is not currently CONNECTED
        (already disconnected, or changed concurrently since the lookup
        below) raises InvalidProviderConnectionStateError -- the
        repository's conditional UPDATE (WHERE status == CONNECTED) is
        what actually decides this at the database level, not the
        earlier, possibly-stale read.
        """
        try:
            connection = self._connections.get_by_id(tenant_id, connection_id)
            if connection is None:
                raise ProviderConnectionNotFoundError()

            affected = self._connections.disconnect(
                tenant_id, connection_id, datetime.now(timezone.utc)
            )
            if affected == 0:
                raise InvalidProviderConnectionStateError(
                    "Provider connection is not currently connected."
                )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        # disconnect() above is a Core UPDATE executed directly via the
        # repository -- it does not update this already-loaded ORM
        # instance's in-memory attributes the way an ORM-level attribute
        # assignment would, so an explicit refresh is required to return
        # accurate post-disconnect state (matching
        # CampaignDeploymentService's mark_* methods, which refresh for
        # the same reason).
        self._session.refresh(connection)
        return connection

    def get_connection(
        self, *, tenant_id: uuid.UUID, connection_id: uuid.UUID
    ) -> ProviderConnection:
        """Return one tenant-scoped connection, or raise (missing/cross-tenant).

        Read-only: never mutates, never decrypts credentials.
        """
        connection = self._connections.get_by_id(tenant_id, connection_id)
        if connection is None:
            raise ProviderConnectionNotFoundError()
        return connection

    def list_connections(self, *, tenant_id: uuid.UUID) -> list[ProviderConnection]:
        """Return every connection for one tenant.

        Metadata only -- read-only, never mutates, never decrypts
        credentials.
        """
        return self._connections.list_by_tenant(tenant_id)

    def get_decrypted_credentials(
        self, *, tenant_id: uuid.UUID, provider: Provider
    ) -> bytes:
        """Return the decrypted credential bytes for one tenant's
        currently connected provider.

        Internal-only seam for a future adapter-integration caller: no
        API route exists (or should ever exist) that returns this value
        to a client. Raises ProviderConnectionNotFoundError if there is
        no CONNECTED row with usable ciphertext for this
        (tenant, provider) pair -- missing, cross-tenant, and
        disconnected are all indistinguishable here, matching this
        codebase's existing not-found pattern. Decryption failures
        (CredentialDecryptionError) propagate unchanged: this is a pure
        read with nothing to roll back. Returns plain bytes only -- never
        parses the payload, never caches the result, never returns the
        ORM object.
        """
        connection = self._connections.get_connected_by_provider(tenant_id, provider)
        if connection is None:
            raise ProviderConnectionNotFoundError()

        # get_connected_by_provider's predicates and the DB CHECK already
        # require non-NULL ciphertext for CONNECTED rows. An explicit
        # guard is used instead of `assert` so python -O cannot strip it
        # and turn an impossible state into an unhandled TypeError from
        # Fernet.decrypt(None).
        if connection.encrypted_credentials is None:
            raise ProviderConnectionNotFoundError()
        return self._encryption.decrypt_credentials(
            tenant_id, provider, connection.encrypted_credentials
        )
