"""SQLAlchemy declarative base and shared model metadata."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models.

    TODO: Import this Base into every model module so Alembic's
    autogenerate can discover table metadata via Base.metadata.
    """
