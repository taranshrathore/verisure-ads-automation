"""SystemRoleAssignment model: a platform-wide grant of a system role."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class SystemRoleAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A platform-wide (cross-tenant) grant of a system role.

    Assignments are soft-revoked (revoked_at), never deleted. System roles
    grant authority across tenants and must never be reachable through any
    tenant-scoped API or service path.

    TODO: Restrict grant/revoke of system roles to platform-administration
    flows once RoleManagementService and system endpoints exist.
    """

    __tablename__ = "system_role_assignments"
    __table_args__ = (
        CheckConstraint(
            "system_role IN ('super_admin')",
            name="ck_system_role_assignments_system_role",
        ),
        Index(
            "uq_system_role_assignments_user_id_system_role_active",
            "user_id",
            "system_role",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_system_role_assignments_user_id_users"),
        nullable=False,
        index=True,
    )
    system_role: Mapped[str] = mapped_column(String(50), nullable=False)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_system_role_assignments_assigned_by_user_id_users",
        ),
        nullable=True,
        index=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_system_role_assignments_revoked_by_user_id_users",
        ),
        nullable=True,
        index=True,
    )

    user: Mapped["User"] = relationship(
        foreign_keys="[SystemRoleAssignment.user_id]",
        lazy="selectin",
    )
    assigned_by: Mapped["User | None"] = relationship(
        foreign_keys="[SystemRoleAssignment.assigned_by_user_id]",
    )
    revoked_by: Mapped["User | None"] = relationship(
        foreign_keys="[SystemRoleAssignment.revoked_by_user_id]",
    )
