"""UserRoleAssignment model: a tenant-scoped grant of a role to a user."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.role import Role
    from app.models.user import User


class UserRoleAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant-scoped grant of a role to a user.

    Assignments are soft-revoked (revoked_at), never deleted, so grant and
    revoke history remains auditable on the row itself.

    TODO: Enforce role/tenant compatibility (built-in or same-tenant custom
    role) and last-tenant-administrator protection in RoleManagementService;
    this model intentionally contains no such validation logic.
    """

    __tablename__ = "user_role_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_user_role_assignments_user_id_tenant_id_users",
        ),
        Index(
            "uq_user_role_assignments_user_id_role_id_active",
            "user_id",
            "role_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_user_role_assignments_user_id_tenant_id_active",
            "user_id",
            "tenant_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "roles.id", name="fk_user_role_assignments_role_id_roles"
        ),
        nullable=False,
        index=True,
    )
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_user_role_assignments_assigned_by_user_id_users",
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
            name="fk_user_role_assignments_revoked_by_user_id_users",
        ),
        nullable=True,
        index=True,
    )

    user: Mapped["User"] = relationship(
        foreign_keys="[UserRoleAssignment.user_id, UserRoleAssignment.tenant_id]",
        lazy="selectin",
    )
    role: Mapped["Role"] = relationship(lazy="selectin")
    assigned_by: Mapped["User | None"] = relationship(
        foreign_keys="[UserRoleAssignment.assigned_by_user_id]",
    )
    revoked_by: Mapped["User | None"] = relationship(
        foreign_keys="[UserRoleAssignment.revoked_by_user_id]",
    )
