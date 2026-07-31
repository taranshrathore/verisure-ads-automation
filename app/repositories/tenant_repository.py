"""Tenant persistence repository."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenant import Tenant


class TenantRepository:
    """Data-access helpers for Tenant rows. Does not commit or roll back."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        """Return a tenant by primary key, or None if not found."""
        return self._session.scalar(select(Tenant).where(Tenant.id == tenant_id))

    def get_by_slug(self, slug: str) -> Tenant | None:
        """Return a tenant by slug, or None if not found."""
        return self._session.scalar(select(Tenant).where(Tenant.slug == slug))

    def lock_for_update(self, tenant_id: UUID) -> Tenant | None:
        """Acquire a row-level lock (SELECT ... FOR UPDATE) on a tenant row.

        Used to serialize operations that could reduce a tenant's active
        administrator count below one: the caller must acquire this lock
        first, then evaluate the post-operation admin count, then mutate,
        all within the same transaction. See RoleManagementService for the
        exact invariant this backs.
        """
        return self._session.scalar(
            select(Tenant).where(Tenant.id == tenant_id).with_for_update()
        )
