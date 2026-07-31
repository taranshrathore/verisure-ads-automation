"""Platform-wide system-role management: internal-only for this milestone.

No HTTP endpoint exposes these operations yet (see docs/HANDOFF.md SS16/SS18:
the platform-tenant bootstrap chicken-and-egg problem -- creating the very
first super_admin without an existing super_admin to authorize it -- is
unresolved). This service exists for direct use by tests and future
admin-tooling/bootstrap scripts until a secure bootstrap mechanism exists.

Owns all transaction commits; repositories never commit.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.authorization.catalog import PLATFORM_TENANT_SLUG, SystemRoleSlug
from app.core.exceptions import (
    PlatformTenantRequiredError,
    RoleAssignmentConflictError,
    RoleNotFoundError,
    UserNotFoundError,
)
from app.models.system_role_assignment import SystemRoleAssignment
from app.repositories.system_role_assignment_repository import (
    SystemRoleAssignmentRepository,
)
from app.repositories.user_repository import UserRepository

# Only this constraint's violation is translated to RoleAssignmentConflictError;
# any other IntegrityError (e.g. an unrelated NOT NULL/FK violation) is
# rolled back and re-raised as-is rather than mislabeled as a duplicate grant.
_DUPLICATE_SYSTEM_ROLE_CONSTRAINT = "uq_system_role_assignments_user_id_system_role_active"


def _constraint_name(exc: IntegrityError) -> str | None:
    """Best-effort extraction of the violated constraint's name (psycopg diagnostics)."""
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


class SystemRoleManagementService:
    """Grants and revokes platform-wide system-role assignments."""

    def __init__(
        self,
        system_role_assignment_repository: SystemRoleAssignmentRepository,
        user_repository: UserRepository,
        session: Session,
    ) -> None:
        self._assignments = system_role_assignment_repository
        self._users = user_repository
        self._session = session

    def assign_system_role(
        self, user_id: UUID, system_role: SystemRoleSlug
    ) -> SystemRoleAssignment:
        """Grant a system role, re-verifying platform-tenant membership at write time.

        Never trusts a cached AuthorizationContext for this: re-fetches the
        user and its tenant fresh, mirroring the read-side fail-closed
        check in AuthorizationService.build_context.
        """
        user = self._users.get_by_id_unscoped(user_id)
        if user is None or user.deleted_at is not None:
            raise UserNotFoundError()
        if user.tenant.slug != PLATFORM_TENANT_SLUG:
            raise PlatformTenantRequiredError()

        existing = self._assignments.get_active_assignment(
            user_id, system_role.value
        )
        if existing is not None:
            raise RoleAssignmentConflictError()

        assignment = SystemRoleAssignment(user_id=user_id, system_role=system_role.value)
        self._assignments.create(assignment)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            if _constraint_name(exc) == _DUPLICATE_SYSTEM_ROLE_CONSTRAINT:
                raise RoleAssignmentConflictError() from exc
            raise
        except Exception:
            self._session.rollback()
            raise
        return assignment

    def revoke_system_role(
        self, assignment_id: UUID, revoked_by_user_id: UUID | None = None
    ) -> None:
        """Revoke a system-role assignment by id."""
        assignment = self._assignments.get_by_id(assignment_id)
        if assignment is None or assignment.revoked_at is not None:
            raise RoleNotFoundError("System role assignment not found.")

        try:
            self._assignments.revoke(
                assignment_id, revoked_by_user_id, datetime.now(timezone.utc)
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
