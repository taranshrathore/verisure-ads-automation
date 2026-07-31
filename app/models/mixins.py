"""Reusable SQLAlchemy mixins shared across ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPrimaryKeyMixin:
    """Adds a UUID primary key column to a model."""

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """Adds created_at / updated_at audit timestamp columns to a model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    """Adds a nullable deleted_at column for future soft-delete support.

    TODO: Implement soft-delete query filtering / deletion logic (e.g. a
    session event listener or repository-level filter) once deletion
    semantics are decided. No delete behavior is implemented yet.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
