"""Authentication API endpoints: login, refresh, logout, and logout-all."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import get_auth_service, get_current_user
from app.models.user import User
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Request body for POST /auth/login."""

    model_config = ConfigDict(extra="forbid")

    tenant_slug: str
    email: str
    password: str


class RefreshRequest(BaseModel):
    """Request body for POST /auth/refresh."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class LogoutRequest(BaseModel):
    """Request body for POST /auth/logout."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class TokenResponse(BaseModel):
    """Response body containing an access/refresh token pair."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str


def _unauthorized(exc: ValueError) -> HTTPException:
    """Convert an AuthService ValueError into a 401 HTTPException."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=str(exc),
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    response_model=TokenResponse,
    response_model_exclude_none=True,
    summary="Authenticate a tenant-scoped user",
    description="Verify tenant slug, email, and password, then issue a new "
    "access/refresh token pair.",
)
def login(
    payload: LoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Authenticate a tenant-scoped user and issue a new token pair."""
    try:
        token_pair = auth_service.login(
            tenant_slug=payload.tenant_slug,
            email=payload.email,
            password=payload.password,
        )
    except ValueError as exc:
        raise _unauthorized(exc) from exc

    return TokenResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
    )


@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    response_model=TokenResponse,
    response_model_exclude_none=True,
    summary="Rotate a refresh token",
    description="Exchange a valid, unexpired refresh token for a new "
    "access/refresh token pair. Reuse of an already-rotated token revokes "
    "the entire token family.",
)
def refresh(
    payload: RefreshRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Rotate a refresh token and issue a new access/refresh token pair."""
    try:
        token_pair = auth_service.refresh(payload.refresh_token)
    except ValueError as exc:
        raise _unauthorized(exc) from exc

    return TokenResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a single refresh token",
    description="Revoke the provided refresh token, ending that session.",
)
def logout(
    payload: LogoutRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> Response:
    """Revoke a single refresh token."""
    try:
        auth_service.logout(payload.refresh_token)
    except ValueError as exc:
        raise _unauthorized(exc) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke all sessions for the authenticated user",
    description="Revoke every active refresh token belonging to the "
    "currently authenticated user, signing them out everywhere.",
)
def logout_all(
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
) -> Response:
    """Revoke all active refresh tokens for the authenticated user."""
    try:
        auth_service.logout_all(
            tenant_id=current_user.tenant_id, user_id=current_user.id
        )
    except ValueError as exc:
        raise _unauthorized(exc) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
