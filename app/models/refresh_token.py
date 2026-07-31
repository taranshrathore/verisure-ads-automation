"""RefreshToken model: persisted, hashed refresh tokens with rotation tracking."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A persisted, hashed refresh token supporting rotation and reuse detection.

    TODO: Wire tenant-aware repository queries and reuse-detection /
    family-revocation logic once the repository and service layers exist.
    No raw token value is ever stored here, only its hash.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_refresh_tokens_user_id_users"),
        nullable=False,
        index=True,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    replaced_by_token_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "refresh_tokens.id",
            name="fk_refresh_tokens_replaced_by_token_id_refresh_tokens",
        ),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    user: Mapped["User"] = relationship(lazy="selectin")
    replaced_by: Mapped["RefreshToken | None"] = relationship(
        remote_side="RefreshToken.id",
        foreign_keys=[replaced_by_token_id],
    )
