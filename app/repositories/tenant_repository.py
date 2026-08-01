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
