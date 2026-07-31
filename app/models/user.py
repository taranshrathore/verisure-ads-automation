"""User model: an authenticated actor scoped to exactly one tenant."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A user account belonging to exactly one tenant.

    TODO: Replace the flat `role` column with a dedicated roles/permissions
    model once RBAC requirements are defined.
    TODO: Add OAuth provider linkage once OAuth is in scope.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_id_email"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_users_tenant_id_tenants"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="member")

    tenant: Mapped["Tenant"] = relationship(
        back_populates="users", lazy="selectin"
    )
