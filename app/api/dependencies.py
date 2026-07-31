"""Shared FastAPI dependency providers for the API layer."""

from collections.abc import Iterator
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy.orm import Session

from app.core.authorization.catalog import PLATFORM_TENANT_SLUG
from app.core.authorization.context import AuthorizationContext
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
from app.repositories.authorization_repository import AuthorizationRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.repositories.user_role_assignment_repository import (
    UserRoleAssignmentRepository,
)
from app.services.auth_service import AuthService
from app.services.authorization_service import AuthorizationService
from app.services.role_management_service import RoleManagementService

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


def get_authorization_repository(
    db: Session = Depends(get_db),
) -> AuthorizationRepository:
    """Provide an AuthorizationRepository bound to the request session."""
    return AuthorizationRepository(db)


def get_authorization_service(
    authorization_repository: AuthorizationRepository = Depends(
        get_authorization_repository
    ),
) -> AuthorizationService:
    """Provide a freshly constructed AuthorizationService for the current request."""
    return AuthorizationService(authorization_repository)


def get_role_repository(db: Session = Depends(get_db)) -> RoleRepository:
    """Provide a RoleRepository bound to the request session."""
    return RoleRepository(db)


def get_user_role_assignment_repository(
    db: Session = Depends(get_db),
) -> UserRoleAssignmentRepository:
    """Provide a UserRoleAssignmentRepository bound to the request session."""
    return UserRoleAssignmentRepository(db)


def get_role_management_service(
    db: Session = Depends(get_db),
    role_repository: RoleRepository = Depends(get_role_repository),
    user_role_assignment_repository: UserRoleAssignmentRepository = Depends(
        get_user_role_assignment_repository
    ),
    user_repository: UserRepository = Depends(get_user_repository),
    tenant_repository: TenantRepository = Depends(get_tenant_repository),
) -> RoleManagementService:
    """Provide a freshly constructed RoleManagementService for the current request."""
    return RoleManagementService(
        role_repository=role_repository,
        user_role_assignment_repository=user_role_assignment_repository,
        user_repository=user_repository,
        tenant_repository=tenant_repository,
        session=db,
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
    """Resolve the authenticated, active user from a validated JWT access token."""
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


def get_authorization_context(
    current_user: User = Depends(get_current_user),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AuthorizationContext:
    """Build the caller's frozen authorization snapshot.

    FastAPI's per-request dependency cache guarantees this runs at most once
    per request, no matter how many permission checks the endpoint declares.
    """
    return authorization_service.build_context(
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        is_platform_tenant=current_user.tenant.slug == PLATFORM_TENANT_SLUG,
    )
