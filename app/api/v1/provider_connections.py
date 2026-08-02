"""Provider connection endpoints.

PHASE 5 SCOPE: HTTP + dependency-injection wiring for the already-built
ProviderConnectionService (see
app/services/provider_connection_service.py). Safe, provider-neutral
metadata endpoints only -- list, retrieve, disconnect. There is no
connect / credential-upload / OAuth callback endpoint yet: those wait
for a real provider OAuth contract. encrypted_credentials and decrypted
credential bytes are never serialized into any response below.

AUTHORIZATION STATE: every route below enforces authentication only
(Depends(get_current_user)); there is no local RBAC/permission check.
Tenant identity is derived exclusively from the authenticated user's
token; it can never appear in a request body or path parameter.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Body, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_current_user, get_provider_connection_service
from app.core.providers import Provider
from app.models.provider_connection import ProviderConnection, ProviderConnectionStatus
from app.models.user import User
from app.services.provider_connection_service import ProviderConnectionService

router = APIRouter(prefix="/provider-connections", tags=["provider-connections"])


class ProviderConnectionRead(BaseModel):
    """Safe metadata for one provider connection.

    Deliberately omits encrypted_credentials (and any decrypted
    credential material): those are internal to
    ProviderConnectionService.get_decrypted_credentials and must never
    leave the service boundary via an API schema.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    provider: Provider
    external_account_id: str | None
    display_name: str | None
    credentials_expire_at: datetime | None
    status: ProviderConnectionStatus
    disconnected_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, connection: ProviderConnection) -> "ProviderConnectionRead":
        """Build a ProviderConnectionRead from a ProviderConnection ORM instance."""
        return cls(
            id=connection.id,
            provider=connection.provider,
            external_account_id=connection.external_account_id,
            display_name=connection.display_name,
            credentials_expire_at=connection.credentials_expire_at,
            status=connection.status,
            disconnected_at=connection.disconnected_at,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
        )


class ProviderConnectionListResponse(BaseModel):
    """Response body for GET /provider-connections."""

    model_config = ConfigDict(extra="forbid")

    items: list[ProviderConnectionRead] = Field(default_factory=list)


class ProviderConnectionDisconnectRequest(BaseModel):
    """Empty request body for POST .../disconnect.

    Exists solely so unexpected client-supplied fields are rejected
    (extra="forbid") rather than silently ignored. Disconnect takes no
    meaningful request parameters -- tenant identity comes from the
    authenticated user, and connection_id is a path parameter.
    """

    model_config = ConfigDict(extra="forbid")


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=ProviderConnectionListResponse,
    summary="List provider connections for the current tenant",
    description="Return every provider connection for the caller's "
    "tenant as safe metadata only. Never includes encrypted or "
    "decrypted credential material.",
)
def list_provider_connections(
    current_user: User = Depends(get_current_user),
    connection_service: ProviderConnectionService = Depends(
        get_provider_connection_service
    ),
) -> ProviderConnectionListResponse:
    """Return a tenant-scoped list of provider-connection metadata."""
    connections = connection_service.list_connections(tenant_id=current_user.tenant_id)
    return ProviderConnectionListResponse(
        items=[ProviderConnectionRead.from_model(c) for c in connections]
    )


@router.get(
    "/{connection_id}",
    status_code=status.HTTP_200_OK,
    response_model=ProviderConnectionRead,
    summary="Retrieve a single provider connection",
    description="Return safe metadata for one provider connection owned "
    "by the caller's tenant. A connection belonging to another tenant "
    "is indistinguishable from a missing one (404). Never includes "
    "encrypted or decrypted credential material.",
)
def get_provider_connection(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    connection_service: ProviderConnectionService = Depends(
        get_provider_connection_service
    ),
) -> ProviderConnectionRead:
    """Return one tenant-scoped provider connection as safe metadata."""
    connection = connection_service.get_connection(
        tenant_id=current_user.tenant_id, connection_id=connection_id
    )
    return ProviderConnectionRead.from_model(connection)


@router.post(
    "/{connection_id}/disconnect",
    status_code=status.HTTP_200_OK,
    response_model=ProviderConnectionRead,
    summary="Disconnect a provider connection",
    description="Disconnect a currently connected provider connection "
    "for the caller's tenant, destroying its stored credentials. A "
    "second disconnect returns 409; a connection belonging to another "
    "tenant is indistinguishable from a missing one (404).",
)
def disconnect_provider_connection(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    connection_service: ProviderConnectionService = Depends(
        get_provider_connection_service
    ),
    _payload: ProviderConnectionDisconnectRequest = Body(
        default_factory=ProviderConnectionDisconnectRequest
    ),
) -> ProviderConnectionRead:
    """Disconnect a tenant-scoped provider connection."""
    connection = connection_service.disconnect(
        tenant_id=current_user.tenant_id, connection_id=connection_id
    )
    return ProviderConnectionRead.from_model(connection)
