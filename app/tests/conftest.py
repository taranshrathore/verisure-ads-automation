"""Shared pytest fixtures for API tests.

Database safety: pytest_configure below fails closed at startup (before any
test collection or run) unless TEST_DATABASE_URL is set, differs from
DATABASE_URL, and names a database that is clearly a dedicated test
database. See app/tests/database.py for the exact rule and README.md for
setup instructions. Nothing here ever touches app.database.session (the
application's own DATABASE_URL-backed engine).

Test isolation: each test runs inside one outer transaction, opened on a
dedicated connection to the test database, with the application's
get_db dependency overridden to yield a Session bound to that same
connection (join_transaction_mode="create_savepoint" -- see SQLAlchemy's
"Joining a Session into an External Transaction" recipe). This means:

- Application code (repositories, services) may call session.commit()
  exactly as in production; each commit only releases a SAVEPOINT, never
  the real outer transaction.
- Teardown always rolls back the outer transaction in a finally block, so
  no row created by a test -- passed, failed, or interrupted -- can
  survive it.
- get_db is the *only* overridden dependency. Authentication
  (get_current_user) runs unmodified, against this same session, so tests
  exercise the real end-to-end chain.

TEMPORARY / CRM-MIGRATION STATE: the local RBAC engine (and its
authorization_fixture/authorization_context_factory fixtures) has been
removed -- see docs/HANDOFF.md. auth_fixture below only creates a
tenant + user + bearer token; it carries no role or permission data.
"""

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.security.jwt import create_access_token
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User
from app.tests.database import (
    TestDatabaseConfigurationError,
    get_test_engine,
    resolve_test_database_url,
)


def pytest_configure(config: pytest.Config) -> None:
    """Fail closed before any test runs if TEST_DATABASE_URL is unsafe."""
    del config
    try:
        resolve_test_database_url()
    except TestDatabaseConfigurationError as exc:
        raise pytest.UsageError(str(exc)) from exc


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Yield a session whose entire effect is rolled back at teardown.

    Binds a Session to a single Connection that already has an outer
    transaction open, using join_transaction_mode="create_savepoint" so
    that any session.commit() call made by application code (or by test
    setup code below) only commits a SAVEPOINT. The real transaction is
    rolled back in the finally block unconditionally.
    """
    engine = get_test_engine()
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """Yield a TestClient with only get_db overridden, to the test session.

    Authentication is never overridden; it executes for real against
    db_session.
    """

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def auth_fixture(db_session: Session):
    """Factory fixture that builds authenticated test users on demand.

    Returns a callable ``make(...)`` that creates a tenant + user directly
    in the test database via db_session, returning ``(user, bearer_token)``.
    This only exercises identity/authentication: no role or permission data
    is created, since local authorization has been removed pending CRM
    integration. No explicit cleanup is required here: the db_session
    fixture's outer-transaction rollback discards every row created by
    this factory.
    """
    suffix = uuid.uuid4().hex[:10]
    counters = {"tenant": 0, "user": 0}

    def make(
        *,
        user_deleted: bool = False,
        tenant_deleted: bool = False,
    ) -> tuple[User, str]:
        """Create a tenant + user matching the requested active/inactive state."""
        counters["tenant"] += 1
        tenant = Tenant(
            name=f"Auth Test Tenant {suffix}-{counters['tenant']}",
            slug=f"auth-test-{suffix}-{counters['tenant']}",
            deleted_at=datetime.now(timezone.utc) if tenant_deleted else None,
        )
        db_session.add(tenant)
        db_session.flush()

        counters["user"] += 1
        user = User(
            tenant_id=tenant.id,
            email=f"auth-test-{suffix}-{counters['user']}@example.com",
            hashed_password="not-a-real-password-hash",
            role="member",
            deleted_at=datetime.now(timezone.utc) if user_deleted else None,
        )
        db_session.add(user)
        db_session.flush()

        token = create_access_token(user_id=user.id, tenant_id=tenant.id)
        return user, token

    return make
