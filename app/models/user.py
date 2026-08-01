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

    TODO: `role` (flat string) predates RBAC and is not used for
    authorization decisions. VeriSure CRM is intended to become the
    source of truth for roles/permissions (see docs/HANDOFF.md); whether
    this column is still needed is a decision for that integration work,
    not a local RBAC model.
    TODO: Add OAuth provider linkage once OAuth is in scope.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_id_email"),
        # Referenced side of Campaign's composite FK
        # (created_by_user_id, tenant_id) -> users(id, tenant_id): guarantees
        # at the database level that a campaign's creator genuinely belongs
        # to that campaign's own tenant.
        UniqueConstraint("id", "tenant_id", name="uq_users_id_tenant_id"),
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
