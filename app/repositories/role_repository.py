"""Role persistence repository.

Tenant scoping happens inside the queries themselves (get_role_for_tenant,
get_active_custom_role_by_slug, list_for_tenant) rather than by fetching
globally and relying on a later service-side check. Does not commit or
roll back.
"""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.permission import Permission
from app.models.role import Role
from app.models.role_permission import RolePermission


class RoleRepository:
    """Data-access helpers for Role and RolePermission rows."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_role_for_tenant(self, tenant_id: UUID, role_id: UUID) -> Role | None:
        """Return a role visible to a tenant (builtin or tenant-owned), or None."""
        return self._session.scalar(
            select(Role).where(
                Role.id == role_id,
                (Role.tenant_id.is_(None)) | (Role.tenant_id == tenant_id),
            )
        )

    def get_active_custom_role_by_slug(self, tenant_id: UUID, slug: str) -> Role | None:
        """Return a tenant's active (non-soft-deleted) custom role by slug, or None."""
        return self._session.scalar(
            select(Role).where(
                Role.tenant_id == tenant_id,
                Role.slug == slug,
                Role.deleted_at.is_(None),
            )
        )

    def list_for_tenant(self, tenant_id: UUID) -> list[Role]:
        """Return every role visible to a tenant: builtins plus its active custom roles."""
        stmt = select(Role).where(
            (Role.tenant_id.is_(None))
            | ((Role.tenant_id == tenant_id) & (Role.deleted_at.is_(None)))
        )
        return list(self._session.scalars(stmt))

    def create(self, role: Role) -> None:
        """Stage a new role for persistence."""
        self._session.add(role)

    def get_permission_slugs_for_role(self, role_id: UUID) -> list[str]:
        """Return the permission slugs currently attached to a role."""
        stmt = (
            select(Permission.slug)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
        return list(self._session.scalars(stmt))

    def get_permission_ids_by_slugs(self, slugs: list[str]) -> dict[str, UUID]:
        """Return a mapping of permission slug -> id for the given slugs."""
        stmt = select(Permission.slug, Permission.id).where(Permission.slug.in_(slugs))
        return {slug: permission_id for slug, permission_id in self._session.execute(stmt)}

    def replace_permissions(self, role_id: UUID, permission_ids: list[UUID]) -> None:
        """Replace a role's entire permission set with the given permission ids."""
        self._session.execute(
            delete(RolePermission).where(RolePermission.role_id == role_id)
        )
        for permission_id in permission_ids:
            self._session.add(
                RolePermission(role_id=role_id, permission_id=permission_id)
            )
