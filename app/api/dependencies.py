"""Shared FastAPI dependency providers for the API layer.

TEMPORARY / CRM-MIGRATION STATE: the database-backed authorization engine
(get_authorization_context, AuthorizationService/Repository, and the
require_permission/require_system_role dependency factories) was removed
along with the rest of the local RBAC implementation -- see
docs/HANDOFF.md. get_current_user below is currently the only
authorization primitive endpoints can depend on: it proves the caller
holds a valid access token for an active user of an active tenant, and
nothing more. It grants no permissions and must never be treated as if it
did.

Seam for future CRM integration: once the CRM authorization contract
(token issuance, claim shape, or permission-lookup API) is known, add a
CRM-backed dependency here (e.g. a get_authorization_context that calls a
CrmAuthorizationProvider) and have endpoints depend on it instead of -- or
in addition to -- get_current_user. No such dependency is invented here in
its absence. Frontend-supplied role/permission values must never be
trusted directly by any endpoint.
"""

from collections.abc import Iterator
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy.orm import Session

from app.adapters.registry import ProviderAdapterRegistry
from app.core.exceptions import (
    InvalidAccessTokenError,
    TenantInactiveError,
    TenantNotFoundError,
    UserInactiveError,
    UserNotFoundError,
)
from app.core.security.jwt import decode_access_token
from app.database.session import SessionFactory
from app.models.user import User
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.services.campaign_deployment_service import CampaignDeploymentService
from app.services.campaign_service import CampaignService
from app.services.campaign_spec_builder import CampaignSpecBuilder
from app.services.publish_campaign_service import PublishCampaignService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")


def get_db() -> Iterator[Session]:
    """Yield a request-scoped SQLAlchemy session, always closed afterward."""
    session_factory = SessionFactory()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_tenant_repository(db: Session = Depends(get_db)) -> TenantRepository:
    """Provide a TenantRepository bound to the request session."""
    return TenantRepository(db)


def get_user_repository(db: Session = Depends(get_db)) -> UserRepository:
    """Provide a UserRepository bound to the request session."""
    return UserRepository(db)


def get_refresh_token_repository(
    db: Session = Depends(get_db),
) -> RefreshTokenRepository:
    """Provide a RefreshTokenRepository bound to the request session."""
    return RefreshTokenRepository(db)


def get_auth_service(
    db: Session = Depends(get_db),
    tenant_repository: TenantRepository = Depends(get_tenant_repository),
    user_repository: UserRepository = Depends(get_user_repository),
    refresh_token_repository: RefreshTokenRepository = Depends(
        get_refresh_token_repository
    ),
) -> AuthService:
    """Provide a freshly constructed AuthService for the current request."""
    return AuthService(
        tenant_repository=tenant_repository,
        user_repository=user_repository,
        refresh_token_repository=refresh_token_repository,
        session=db,
    )


def get_campaign_repository(db: Session = Depends(get_db)) -> CampaignRepository:
    """Provide a CampaignRepository bound to the request session."""
    return CampaignRepository(db)


def get_campaign_service(
    db: Session = Depends(get_db),
    campaign_repository: CampaignRepository = Depends(get_campaign_repository),
) -> CampaignService:
    """Provide a freshly constructed CampaignService for the current request."""
    return CampaignService(campaign_repository=campaign_repository, session=db)


def get_campaign_deployment_repository(
    db: Session = Depends(get_db),
) -> CampaignDeploymentRepository:
    """Provide a CampaignDeploymentRepository bound to the request session."""
    return CampaignDeploymentRepository(db)


def get_campaign_deployment_service(
    db: Session = Depends(get_db),
    deployment_repository: CampaignDeploymentRepository = Depends(
        get_campaign_deployment_repository
    ),
) -> CampaignDeploymentService:
    """Provide a freshly constructed CampaignDeploymentService for the current request."""
    return CampaignDeploymentService(deployment_repository, db)


def get_campaign_spec_builder() -> CampaignSpecBuilder:
    """Provide a CampaignSpecBuilder.

    Stateless (a plain staticmethod holder) -- safe to construct fresh
    per request rather than sharing a singleton.
    """
    return CampaignSpecBuilder()


def get_provider_adapter_registry() -> ProviderAdapterRegistry:
    """Provide a ProviderAdapterRegistry.

    Stateless today (its adapters hold no connection/session/mutable
    state of their own -- see app/adapters/registry.py), so a fresh
    instance per request is constructed here rather than sharing a
    global singleton.
    """
    return ProviderAdapterRegistry()


def get_publish_campaign_service(
    db: Session = Depends(get_db),
    campaign_repository: CampaignRepository = Depends(get_campaign_repository),
    deployment_repository: CampaignDeploymentRepository = Depends(
        get_campaign_deployment_repository
    ),
    deployment_service: CampaignDeploymentService = Depends(
        get_campaign_deployment_service
    ),
    spec_builder: CampaignSpecBuilder = Depends(get_campaign_spec_builder),
    adapter_registry: ProviderAdapterRegistry = Depends(get_provider_adapter_registry),
) -> PublishCampaignService:
    """Provide a freshly constructed PublishCampaignService for the current request.

    Every dependency here is itself request-scoped (deployment_service
    and campaign_repository/deployment_repository all resolve to the
    same get_db session via FastAPI's per-request dependency caching),
    so there is exactly one Session, one CampaignDeploymentService, and
    one CampaignRepository/CampaignDeploymentRepository pair per
    request -- no duplicate construction paths.
    """
    return PublishCampaignService(
        campaign_repository,
        deployment_repository,
        deployment_service,
        spec_builder,
        adapter_registry,
        db,
    )


def _unauthorized(detail: str) -> HTTPException:
    """Build a standard 401 Unauthorized error with a WWW-Authenticate header."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


# TODO: Every authenticated request currently performs a database lookup for
# both the tenant and the user (see below). This is intentional: it ensures a
# still-valid, unexpired JWT cannot grant access after a user or tenant has
# since been deleted/disabled. If profiling ever shows this lookup is a
# bottleneck, consider caching — but no caching is implemented here yet.
def get_current_user(
    token: str = Depends(oauth2_scheme),
    tenant_repository: TenantRepository = Depends(get_tenant_repository),
    user_repository: UserRepository = Depends(get_user_repository),
) -> User:
    """Resolve the authenticated, active user from a validated JWT access token.

    This proves authentication only. It carries no role or permission
    information -- see the module docstring for the current CRM-migration
    state of authorization in this backend.
    """
    try:
        try:
            claims = decode_access_token(token)
        except PyJWTError as exc:
            raise InvalidAccessTokenError() from exc

        if claims.get("type") != "access":
            raise InvalidAccessTokenError("Invalid token type.")

        raw_tenant_id = claims.get("tenant_id")
        raw_user_id = claims.get("sub")
        if not raw_tenant_id or not raw_user_id:
            raise InvalidAccessTokenError("Malformed token claims.")

        try:
            tenant_id = UUID(raw_tenant_id)
            user_id = UUID(raw_user_id)
        except ValueError as exc:
            raise InvalidAccessTokenError("Malformed token claims.") from exc

        tenant = tenant_repository.get_by_id(tenant_id)
        if tenant is None:
            raise TenantNotFoundError()
        if tenant.deleted_at is not None:
            raise TenantInactiveError()

        user = user_repository.get_by_id(tenant_id, user_id)
        if user is None:
            raise UserNotFoundError()
        if user.deleted_at is not None:
            raise UserInactiveError()

        return user
    except (
        InvalidAccessTokenError,
        TenantNotFoundError,
        TenantInactiveError,
        UserNotFoundError,
        UserInactiveError,
    ) as exc:
        raise _unauthorized(str(exc)) from exc
