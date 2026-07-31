"""User persistence repository."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    """Data-access helpers for User rows. Does not commit or roll back."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, tenant_id: UUID, user_id: UUID) -> User | None:
        """Return a tenant-scoped user by ID, or None if not found."""
        return self._session.scalar(
            select(User).where(User.tenant_id == tenant_id, User.id == user_id)
        )

    def get_by_tenant_and_email(self, tenant_id: UUID, email: str) -> User | None:
        """Return a user by tenant and email, or None if not found."""
        return self._session.scalar(
            select(User).where(User.tenant_id == tenant_id, User.email == email)
        )
