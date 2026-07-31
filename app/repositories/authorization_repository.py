"""Authorization read-model repository: scalar slug queries only.

Three separate, individually indexed queries by design. A combined
roles-to-permissions inner join would silently omit an assigned role that
currently has zero permissions; keeping role enumeration (Q1) and
permission resolution (Q2) apart makes each query trivially auditable.

Returns plain slug strings, never ORM Role/Permission objects, and never
makes authorization decisions. Does not commit or roll back.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.permission import Permission
from app.models.role import Role
from app.models.role_permission import RolePermission
from app.models.system_role_assignment import SystemRoleAssignment
from app.models.user_role_assignment import UserRoleAssignment


class AuthorizationRepository:
    """Read-only authorization queries returning scalar slug lists."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active_tenant_role_slugs(
        self, user_id: UUID, tenant_id: UUID
    ) -> list[str]:
        """Q1: distinct slugs of every active assigned role.

        Includes roles that currently grant zero permissions. Soft-deleted
        roles are excluded defensively even though the write path is
        expected to revoke their assignments.
        """
        stmt = (
            select(Role.slug)
            .distinct()
            .join(UserRoleAssignment, UserRoleAssignment.role_id == Role.id)
            .where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.tenant_id == tenant_id,
                UserRoleAssignment.revoked_at.is_(None),
                Role.deleted_at.is_(None),
            )
        )
        return list(self._session.scalars(stmt))

    def get_effective_permission_slugs(
        self, user_id: UUID, tenant_id: UUID
    ) -> list[str]:
        """Q2: distinct permission slugs granted by the user's active roles.

        Inner joins are correct here: a zero-permission role genuinely
        contributes nothing to this set.
        """
        stmt = (
            select(Permission.slug)
            .distinct()
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRoleAssignment, UserRoleAssignment.role_id == Role.id)
            .where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.tenant_id == tenant_id,
                UserRoleAssignment.revoked_at.is_(None),
                Role.deleted_at.is_(None),
            )
        )
        return list(self._session.scalars(stmt))

    def get_active_system_role_slugs(self, user_id: UUID) -> list[str]:
        """Q3: slugs of the user's active system-role assignments."""
        stmt = select(SystemRoleAssignment.system_role).where(
            SystemRoleAssignment.user_id == user_id,
            SystemRoleAssignment.revoked_at.is_(None),
        )
        return list(self._session.scalars(stmt))
