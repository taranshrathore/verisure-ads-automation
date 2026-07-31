"""SystemRoleAssignment persistence repository. Does not commit or roll back."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.system_role_assignment import SystemRoleAssignment


class SystemRoleAssignmentRepository:
    """Data-access helpers for SystemRoleAssignment rows."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, assignment: SystemRoleAssignment) -> None:
        """Stage a new system-role assignment for persistence."""
        self._session.add(assignment)

    def get_by_id(self, assignment_id: UUID) -> SystemRoleAssignment | None:
        """Return a system-role assignment by id, or None if not found."""
        return self._session.scalar(
            select(SystemRoleAssignment).where(
                SystemRoleAssignment.id == assignment_id
            )
        )

    def get_active_assignment(
        self, user_id: UUID, system_role: str
    ) -> SystemRoleAssignment | None:
        """Return a user's active assignment of a specific system role, or None."""
        return self._session.scalar(
            select(SystemRoleAssignment).where(
                SystemRoleAssignment.user_id == user_id,
                SystemRoleAssignment.system_role == system_role,
                SystemRoleAssignment.revoked_at.is_(None),
            )
        )

    def revoke(
        self,
        assignment_id: UUID,
        revoked_by_user_id: UUID | None,
        revoked_at: datetime,
    ) -> None:
        """Set revoked_at (and revoker) on a system-role assignment."""
        self._session.execute(
            update(SystemRoleAssignment)
            .where(SystemRoleAssignment.id == assignment_id)
            .values(revoked_at=revoked_at, revoked_by_user_id=revoked_by_user_id)
        )
