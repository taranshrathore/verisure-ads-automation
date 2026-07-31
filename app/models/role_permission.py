"""RolePermission model: the join between roles and permissions."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.permission import Permission
    from app.models.role import Role


class RolePermission(Base):
    """Grants a single permission to a single role.

    This join row is hard-deleted state: it either exists or it does not.
    Historical grant/revoke tracking belongs to user_role_assignments, not
    to the role-to-permission mapping itself, so no timestamps or soft
    deletion are used here.
    """

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", name="fk_role_permissions_role_id_roles"),
        primary_key=True,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "permissions.id", name="fk_role_permissions_permission_id_permissions"
        ),
        primary_key=True,
        index=True,
    )

    role: Mapped["Role"] = relationship(lazy="selectin")
    permission: Mapped["Permission"] = relationship(lazy="selectin")
