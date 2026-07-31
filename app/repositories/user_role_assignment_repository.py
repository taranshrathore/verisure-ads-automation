"""UserRoleAssignment persistence repository.

Tenant scoping and role/user validity happen inside the queries themselves,
not via a later service-side filter on globally fetched rows. Does not
commit or roll back.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.authorization.catalog import BuiltInRoleSlug
from app.models.role import Role
from app.models.user import User
from app.models.user_role_assignment import UserRoleAssignment


class UserRoleAssignmentRepository:
    """Data-access helpers for UserRoleAssignment rows."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, assignment: UserRoleAssignment) -> None:
        """Stage a new role assignment for persistence."""
        self._session.add(assignment)

    def get_assignment_for_tenant(
        self, tenant_id: UUID, assignment_id: UUID
    ) -> UserRoleAssignment | None:
        """Return an assignment by id, scoped to the tenant it was granted under."""
        return self._session.scalar(
            select(UserRoleAssignment).where(
                UserRoleAssignment.id == assignment_id,
                UserRoleAssignment.tenant_id == tenant_id,
            )
        )

    def get_active_assignment_for_tenant(
        self, tenant_id: UUID, user_id: UUID, role_id: UUID
    ) -> UserRoleAssignment | None:
        """Return the user's active assignment of a role, scoped to the tenant.

        Joins to Role to additionally confirm the role is builtin or owned
        by this tenant, rather than trusting the caller-supplied role_id
        alone.
        """
        stmt = (
            select(UserRoleAssignment)
            .join(Role, Role.id == UserRoleAssignment.role_id)
            .where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.tenant_id == tenant_id,
                UserRoleAssignment.role_id == role_id,
                UserRoleAssignment.revoked_at.is_(None),
                (Role.tenant_id.is_(None)) | (Role.tenant_id == tenant_id),
            )
        )
        return self._session.scalar(stmt)

    def count_active_assignments_for_role(self, role_id: UUID) -> int:
        """Count active (non-revoked) assignments of a given role, across all tenants."""
        stmt = (
            select(func.count())
            .select_from(UserRoleAssignment)
            .where(
                UserRoleAssignment.role_id == role_id,
                UserRoleAssignment.revoked_at.is_(None),
            )
        )
        return self._session.scalar(stmt) or 0

    def count_active_tenant_admins(self, tenant_id: UUID) -> int:
        """Count users in the tenant holding an active builtin tenant_admin assignment.

        Soft-deleted users and revoked assignments are excluded. This is the
        query the last-tenant-administrator protection check is built on;
        callers must lock the tenant row (TenantRepository.lock_for_update)
        before calling this and before mutating, within one transaction.
        """
        stmt = (
            select(func.count())
            .select_from(UserRoleAssignment)
            .join(Role, Role.id == UserRoleAssignment.role_id)
            .join(User, User.id == UserRoleAssignment.user_id)
            .where(
                UserRoleAssignment.tenant_id == tenant_id,
                UserRoleAssignment.revoked_at.is_(None),
                Role.tenant_id.is_(None),
                Role.slug == BuiltInRoleSlug.TENANT_ADMIN.value,
                User.deleted_at.is_(None),
            )
        )
        return self._session.scalar(stmt) or 0

    def revoke(
        self,
        assignment_id: UUID,
        revoked_by_user_id: UUID | None,
        revoked_at: datetime,
    ) -> None:
        """Set revoked_at (and revoker) on a role assignment."""
        self._session.execute(
            update(UserRoleAssignment)
            .where(UserRoleAssignment.id == assignment_id)
            .values(revoked_at=revoked_at, revoked_by_user_id=revoked_by_user_id)
        )
