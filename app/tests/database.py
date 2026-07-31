"""Dedicated SQLAlchemy engine/session infrastructure for the test suite.

This module never imports app.database.session and never reads
settings.database_url for connectivity: the test suite must be structurally
incapable of connecting to the application's own development database. It
builds its engine exclusively from TEST_DATABASE_URL, validated fail-closed
by resolve_test_database_url() before any connection is attempted.

See the "Testing" section in README.md for how to provision the test
database and set TEST_DATABASE_URL.
"""

from functools import lru_cache
from urllib.parse import urlsplit

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import settings


class TestDatabaseConfigurationError(RuntimeError):
    """Raised when TEST_DATABASE_URL is missing, unsafe, or misconfigured."""


def _database_name(url: str) -> str:
    """Return the path-component database name of a SQLAlchemy URL."""
    return urlsplit(url).path.lstrip("/")


def resolve_test_database_url() -> str:
    """Validate and return TEST_DATABASE_URL, or raise a clear, fail-closed error.

    Three conservative guards, checked in order:

    1. TEST_DATABASE_URL must be set at all -- there is no fallback to
       DATABASE_URL.
    2. TEST_DATABASE_URL must differ from DATABASE_URL -- a copy-paste
       mistake must not silently point tests at the development database.
    3. The database name (the path component of the URL, e.g. "verisure_db"
       in ".../verisure_db") must contain the substring "test"
       (case-insensitive) -- a deliberate, minimal signal that the target
       database was intentionally provisioned for testing.
    """
    test_url = settings.test_database_url
    if not test_url:
        raise TestDatabaseConfigurationError(
            "TEST_DATABASE_URL is not set. The test suite refuses to run "
            "without a dedicated test database rather than silently "
            "falling back to DATABASE_URL. Set TEST_DATABASE_URL to a "
            "PostgreSQL URL whose database name contains 'test' -- see "
            "the 'Testing' section in README.md."
        )

    if settings.database_url and test_url == settings.database_url:
        raise TestDatabaseConfigurationError(
            "TEST_DATABASE_URL is identical to DATABASE_URL. Tests must "
            "never run against the development database. Point "
            "TEST_DATABASE_URL at a separate, dedicated test database."
        )

    dbname = _database_name(test_url)
    if "test" not in dbname.lower():
        raise TestDatabaseConfigurationError(
            f"TEST_DATABASE_URL's database name ({dbname!r}) does not "
            "contain 'test'. This conservative check exists to prevent "
            "the test suite from ever running destructive operations "
            "against a non-test database. Provision a dedicated database "
            "whose name contains 'test' (e.g. 'verisure_test_db') and "
            "point TEST_DATABASE_URL at it -- see the 'Testing' section "
            "in README.md."
        )

    return test_url


@lru_cache
def get_test_engine() -> Engine:
    """Lazily create and cache the SQLAlchemy engine bound to TEST_DATABASE_URL."""
    return create_engine(resolve_test_database_url())


@lru_cache
def TestSessionFactory() -> sessionmaker[Session]:
    """Lazily create and cache the session factory bound to the test engine.

    Unused by conftest.py directly (fixtures construct Session objects bound
    to a single connection for savepoint-based isolation, see
    app/tests/conftest.py), but provided for any future test code that needs
    an ordinary, independent test-database session.
    """
    return sessionmaker(
        bind=get_test_engine(),
        autocommit=False,
        autoflush=False,
        class_=Session,
    )
