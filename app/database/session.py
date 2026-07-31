"""Database engine and session factory for PostgreSQL (sync, SQLAlchemy 2.x)."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import settings


@lru_cache
def get_engine() -> Engine:
    """Lazily create and cache the SQLAlchemy engine.

    DATABASE_URL is validated here, not at import time, so the application
    can still start before PostgreSQL is configured.
    """
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set the DATABASE_URL environment "
            "variable (see .env.example) before using the database."
        )
    # TODO: Tune pool_size, max_overflow, and pool_pre_ping for production load.
    return create_engine(settings.database_url)


@lru_cache
def SessionFactory() -> sessionmaker[Session]:
    """Lazily create and cache the session factory, bound to the lazy engine.

    TODO: Add a request-scoped get_db() dependency once API routes exist.
    """
    return sessionmaker(
        bind=get_engine(),
        autocommit=False,
        autoflush=False,
        class_=Session,
    )


# TODO: Introduce an async engine/session (create_async_engine, AsyncSession)
# when the codebase moves to async I/O.
