"""Role model: a global built-in role or a tenant-owned custom role."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class Role(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A named collection of permissions.

    tenant_id IS NULL and is_builtin=True means a global built-in role
    shared by every tenant. tenant_id set and is_builtin=False means a
    tenant-owned custom role.

    TODO: Seed built-in roles via an idempotent Alembic data migration once
    RBAC seeding is implemented.
    """

    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint(
            "tenant_id IS NULL OR slug NOT IN "
            "('tenant_admin', 'manager', 'employee', 'viewer')",
            name="ck_roles_reserved_slug",
        ),
        CheckConstraint(
            "(tenant_id IS NULL AND is_builtin IS TRUE) OR "
            "(tenant_id IS NOT NULL AND is_builtin IS FALSE)",
            name="ck_roles_scope_matches_builtin",
        ),
        Index(
            "uq_roles_slug_builtin",
            "slug",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
        ),
        Index(
            "uq_roles_tenant_id_slug_custom",
            "tenant_id",
            "slug",
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", name="fk_roles_tenant_id_tenants"),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    tenant: Mapped["Tenant | None"] = relationship(lazy="selectin")
