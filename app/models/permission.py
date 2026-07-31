"""Permission model: a global, code-defined authorization capability."""

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Permission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single, globally unique, code-defined authorization capability.

    Permissions have no tenant scope and no soft deletion: the permission
    catalog is defined by application code, not by runtime tenant data.

    TODO: Seed the permission catalog via an idempotent Alembic data
    migration once RBAC seeding is implemented.
    """

    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_permissions_slug"),
    )

    slug: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
