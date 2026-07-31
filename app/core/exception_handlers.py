"""Centralized FastAPI exception handlers for the application exception hierarchy."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AuthenticationError,
    CrossTenantAccessError,
    LastTenantAdminError,
    PermissionDeniedError,
    PermissionNotFoundError,
    ProtectedRoleError,
    RoleAssignmentConflictError,
    RoleNotFoundError,
    TenantInactiveError,
    TenantNotFoundError,
    UserInactiveError,
    UserNotFoundError,
)


def _json_error(
    status_code: int,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a standard {"detail": message} JSON error response."""
    return JSONResponse(
        status_code=status_code,
        content={"detail": message},
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register centralized exception handlers on the FastAPI application."""

    @app.exception_handler(AuthenticationError)
    async def _handle_authentication_error(
        request: Request, exc: AuthenticationError
    ) -> JSONResponse:
        """Convert an AuthenticationError into a 401 response."""
        return _json_error(
            status.HTTP_401_UNAUTHORIZED,
            str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(TenantNotFoundError)
    async def _handle_tenant_not_found(
        request: Request, exc: TenantNotFoundError
    ) -> JSONResponse:
        """Convert a TenantNotFoundError into a 404 response."""
        return _json_error(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(TenantInactiveError)
    async def _handle_tenant_inactive(
        request: Request, exc: TenantInactiveError
    ) -> JSONResponse:
        """Convert a TenantInactiveError into a 403 response."""
        return _json_error(status.HTTP_403_FORBIDDEN, str(exc))

    @app.exception_handler(UserNotFoundError)
    async def _handle_user_not_found(
        request: Request, exc: UserNotFoundError
    ) -> JSONResponse:
        """Convert a UserNotFoundError into a 404 response."""
        return _json_error(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(UserInactiveError)
    async def _handle_user_inactive(
        request: Request, exc: UserInactiveError
    ) -> JSONResponse:
        """Convert a UserInactiveError into a 403 response."""
        return _json_error(status.HTTP_403_FORBIDDEN, str(exc))

    @app.exception_handler(PermissionDeniedError)
    async def _handle_permission_denied(
        request: Request, exc: PermissionDeniedError
    ) -> JSONResponse:
        """Convert a PermissionDeniedError into a 403 response."""
        return _json_error(status.HTTP_403_FORBIDDEN, str(exc))

    @app.exception_handler(CrossTenantAccessError)
    async def _handle_cross_tenant_access(
        request: Request, exc: CrossTenantAccessError
    ) -> JSONResponse:
        """Convert a CrossTenantAccessError into a 404 response."""
        return _json_error(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(RoleNotFoundError)
    async def _handle_role_not_found(
        request: Request, exc: RoleNotFoundError
    ) -> JSONResponse:
        """Convert a RoleNotFoundError into a 404 response."""
        return _json_error(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(PermissionNotFoundError)
    async def _handle_permission_not_found(
        request: Request, exc: PermissionNotFoundError
    ) -> JSONResponse:
        """Convert a PermissionNotFoundError into a 404 response."""
        return _json_error(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(RoleAssignmentConflictError)
    async def _handle_role_assignment_conflict(
        request: Request, exc: RoleAssignmentConflictError
    ) -> JSONResponse:
        """Convert a RoleAssignmentConflictError into a 409 response."""
        return _json_error(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(ProtectedRoleError)
    async def _handle_protected_role(
        request: Request, exc: ProtectedRoleError
    ) -> JSONResponse:
        """Convert a ProtectedRoleError into a 403 response."""
        return _json_error(status.HTTP_403_FORBIDDEN, str(exc))

    @app.exception_handler(LastTenantAdminError)
    async def _handle_last_tenant_admin(
        request: Request, exc: LastTenantAdminError
    ) -> JSONResponse:
        """Convert a LastTenantAdminError into a 409 response."""
        return _json_error(status.HTTP_409_CONFLICT, str(exc))
