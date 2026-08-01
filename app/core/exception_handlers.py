"""Centralized FastAPI exception handlers for the application exception hierarchy."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AuthenticationError,
    CampaignDeploymentNotFoundError,
    CampaignNotFoundError,
    CampaignValidationError,
    InvalidCampaignDeploymentStateError,
    InvalidCampaignStateError,
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
    """Register centralized exception handlers on the FastAPI application.

    NOTE: handlers for the local RBAC exception hierarchy (PermissionDeniedError,
    CrossTenantAccessError, RoleNotFoundError, PermissionNotFoundError,
    RoleAssignmentConflictError, ProtectedRoleError, LastTenantAdminError,
    PlatformTenantRequiredError) were removed along with that engine -- see
    docs/HANDOFF.md for the CRM migration status.
    """

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

    @app.exception_handler(CampaignNotFoundError)
    async def _handle_campaign_not_found(
        request: Request, exc: CampaignNotFoundError
    ) -> JSONResponse:
        """Convert a CampaignNotFoundError into a 404 response."""
        return _json_error(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(InvalidCampaignStateError)
    async def _handle_invalid_campaign_state(
        request: Request, exc: InvalidCampaignStateError
    ) -> JSONResponse:
        """Convert an InvalidCampaignStateError into a 409 response."""
        return _json_error(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(CampaignValidationError)
    async def _handle_campaign_validation_error(
        request: Request, exc: CampaignValidationError
    ) -> JSONResponse:
        """Convert a CampaignValidationError into a 422 response."""
        return _json_error(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

    @app.exception_handler(CampaignDeploymentNotFoundError)
    async def _handle_campaign_deployment_not_found(
        request: Request, exc: CampaignDeploymentNotFoundError
    ) -> JSONResponse:
        """Convert a CampaignDeploymentNotFoundError into a 404 response."""
        return _json_error(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(InvalidCampaignDeploymentStateError)
    async def _handle_invalid_campaign_deployment_state(
        request: Request, exc: InvalidCampaignDeploymentStateError
    ) -> JSONResponse:
        """Convert an InvalidCampaignDeploymentStateError into a 409 response."""
        return _json_error(status.HTTP_409_CONFLICT, str(exc))
