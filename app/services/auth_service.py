"""Authentication orchestration service: login, token refresh, and logout.

AuthService owns all transaction commits; repositories never commit.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import (
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RefreshTokenExpiredError,
    RefreshTokenRevokedError,
    RefreshTokenReuseError,
    UserNotFoundError,
)
from app.core.security.jwt import create_access_token
from app.core.security.password import verify_password
from app.core.security.tokens import generate_refresh_token, hash_token
from app.models.refresh_token import RefreshToken
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository

# TODO: Move refresh-token lifetime to centralized settings.
_REFRESH_TOKEN_LIFETIME = timedelta(days=30)


@dataclass(frozen=True)
class TokenPair:
    """An access token paired with its opaque refresh token."""

    access_token: str
    refresh_token: str


class AuthService:
    """Orchestrates authentication use cases across repositories."""

    def __init__(
        self,
        tenant_repository: TenantRepository,
        user_repository: UserRepository,
        refresh_token_repository: RefreshTokenRepository,
        session: Session,
    ) -> None:
        self._tenants = tenant_repository
        self._users = user_repository
        self._refresh_tokens = refresh_token_repository
        self._session = session

    def login(self, tenant_slug: str, email: str, password: str) -> TokenPair:
        """Authenticate a user and issue a new access/refresh token pair."""
        tenant = self._tenants.get_by_slug(tenant_slug)
        if tenant is None or tenant.deleted_at is not None:
            raise InvalidCredentialsError()

        user = self._users.get_by_tenant_and_email(tenant.id, email)
        if user is None or user.deleted_at is not None:
            raise InvalidCredentialsError()

        if not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError()

        now = datetime.now(timezone.utc)
        raw_refresh_token = generate_refresh_token()

        refresh_token = RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            family_id=uuid.uuid4(),
            token_hash=hash_token(raw_refresh_token),
            expires_at=now + _REFRESH_TOKEN_LIFETIME,
        )
        self._refresh_tokens.create(refresh_token)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

        access_token = create_access_token(user_id=user.id, tenant_id=tenant.id)

        return TokenPair(access_token=access_token, refresh_token=raw_refresh_token)

    # TODO: This implementation is vulnerable to concurrent refresh requests
    # using the same token (a TOCTOU race): two simultaneous calls can both
    # read the token as valid before either commits, resulting in two
    # rotations from the same parent. Upgrade this to either a
    # SELECT ... FOR UPDATE row lock on the existing token before rotating,
    # or an atomic conditional UPDATE (e.g. UPDATE ... WHERE id = ? AND
    # revoked_at IS NULL AND replaced_by_token_id IS NULL) and check the
    # affected row count to guarantee only one successful rotation per token.
    def refresh(self, raw_refresh_token: str) -> TokenPair:
        """Validate and rotate a refresh token, revoking its family on reuse."""
        existing = self._refresh_tokens.get_by_token_hash(hash_token(raw_refresh_token))
        if existing is None:
            raise InvalidRefreshTokenError()

        now = datetime.now(timezone.utc)

        if existing.replaced_by_token_id is not None:
            self._refresh_tokens.revoke_family(existing.family_id)
            try:
                self._session.commit()
            except Exception:
                self._session.rollback()
                raise
            raise RefreshTokenReuseError()

        if existing.revoked_at is not None:
            raise RefreshTokenRevokedError()

        if existing.expires_at <= now:
            raise RefreshTokenExpiredError()

        new_token_id = uuid.uuid4()
        raw_new_refresh_token = generate_refresh_token()

        new_token = RefreshToken(
            id=new_token_id,
            user_id=existing.user_id,
            family_id=existing.family_id,
            token_hash=hash_token(raw_new_refresh_token),
            expires_at=now + _REFRESH_TOKEN_LIFETIME,
        )
        self._refresh_tokens.create(new_token)
        self._refresh_tokens.mark_replaced(existing.id, new_token_id)
        self._refresh_tokens.revoke(existing.id)

        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

        access_token = create_access_token(
            user_id=existing.user_id, tenant_id=existing.user.tenant_id
        )

        return TokenPair(access_token=access_token, refresh_token=raw_new_refresh_token)

    def logout(self, raw_refresh_token: str) -> None:
        """Revoke a single refresh token."""
        existing = self._refresh_tokens.get_by_token_hash(hash_token(raw_refresh_token))
        if existing is None:
            raise InvalidRefreshTokenError()

        self._refresh_tokens.revoke(existing.id)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def logout_all(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Revoke all active refresh tokens for a tenant-scoped user."""
        user = self._users.get_by_id(tenant_id, user_id)
        if user is None:
            raise UserNotFoundError()

        self._refresh_tokens.revoke_all_for_user(user_id)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
