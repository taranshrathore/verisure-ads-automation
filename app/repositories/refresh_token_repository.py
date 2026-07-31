"""Refresh-token persistence repository."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    """Data-access helpers for RefreshToken rows. Does not commit or roll back."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, refresh_token: RefreshToken) -> None:
        """Stage a new refresh token for persistence."""
        self._session.add(refresh_token)

    def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        """Return a refresh token by its hash, or None if not found."""
        return self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )

    def mark_replaced(self, token_id: UUID, replaced_by_token_id: UUID) -> None:
        """Set replaced_by_token_id on an existing refresh token."""
        self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(replaced_by_token_id=replaced_by_token_id)
        )

    def revoke(self, token_id: UUID) -> None:
        """Set revoked_at on a single refresh token."""
        self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(revoked_at=datetime.now(timezone.utc))
        )

    def revoke_family(self, family_id: UUID) -> None:
        """Set revoked_at on all active tokens in a family."""
        self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )

    def revoke_all_for_user(self, user_id: UUID) -> None:
        """Set revoked_at on all active tokens for a user."""
        self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
