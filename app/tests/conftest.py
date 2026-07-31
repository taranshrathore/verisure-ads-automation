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
  (get_current_user) and authorization (get_authorization_context,
  require_permission) run unmodified, against this same session, so tests
  exercise the real end-to-end chain.
"""

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.authorization.catalog import PLATFORM_TENANT_SLUG
from app.core.security.jwt import create_access_token
from app.main import app
from app.models.permission import Permission
from app.models.role import Role
from app.models.role_permission import RolePermission
from app.models.system_role_assignment import SystemRoleAssignment
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_role_assignment import UserRoleAssignment
from app.repositories.authorization_repository import AuthorizationRepository
from app.services.authorization_service import AuthorizationService
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

    Authentication and authorization dependencies are never overridden;
    they execute for real against db_session.
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
def authorization_fixture(db_session: Session):
    """Factory fixture that builds authenticated test users on demand.

    Returns a callable ``make(...)`` that creates a tenant + user (and,
    depending on the arguments, a tenant-role assignment or system-role
    assignment) directly in the test database via db_session, returning
    ``(user, bearer_token)``. No explicit cleanup is required here: the
    db_session fixture's outer-transaction rollback discards every row
    created by this factory, including a platform tenant created for a
    super_admin scenario.
    """
    suffix = uuid.uuid4().hex[:10]
    counters = {"tenant": 0, "user": 0}

    def make(
        *,
        role_slug: str | None = None,
        revoked: bool = False,
        grant_custom_role: bool = False,
        soft_delete_role: bool = False,
        system_role: str | None = None,
    ) -> tuple[User, str]:
        """Create a tenant + user matching the requested authorization state."""
        if system_role is not None:
            # Platform-tenant assumption: system-role eligibility requires
            # tenant.slug == PLATFORM_TENANT_SLUG (see AuthorizationService).
            # This fixture creates that tenant fresh inside the test
            # database's rolled-back transaction; it never touches (or
            # assumes the existence of) a platform tenant in any other
            # database.
            tenant = (
                db_session.query(Tenant)
                .filter(Tenant.slug == PLATFORM_TENANT_SLUG)
                .one_or_none()
            )
            if tenant is None:
                tenant = Tenant(name="Platform (test)", slug=PLATFORM_TENANT_SLUG)
                db_session.add(tenant)
                db_session.flush()
        else:
            counters["tenant"] += 1
            tenant = Tenant(
                name=f"Authz Test Tenant {suffix}-{counters['tenant']}",
                slug=f"authz-test-{suffix}-{counters['tenant']}",
            )
            db_session.add(tenant)
            db_session.flush()

        counters["user"] += 1
        user = User(
            tenant_id=tenant.id,
            email=f"authz-test-{suffix}-{counters['user']}@example.com",
            hashed_password="not-a-real-password-hash",
            role="member",
        )
        db_session.add(user)
        db_session.flush()

        if system_role is not None:
            db_session.add(
                SystemRoleAssignment(user_id=user.id, system_role=system_role)
            )
            db_session.flush()

        if grant_custom_role:
            permission = (
                db_session.query(Permission)
                .filter(Permission.slug == "campaigns:read")
                .one()
            )
            role = Role(
                tenant_id=tenant.id,
                slug=f"custom-{suffix}-{counters['user']}",
                name="Custom Test Role",
                is_builtin=False,
            )
            db_session.add(role)
            db_session.flush()

            db_session.add(
                RolePermission(role_id=role.id, permission_id=permission.id)
            )
            db_session.flush()

            if soft_delete_role:
                role.deleted_at = datetime.now(timezone.utc)
                db_session.flush()

            db_session.add(
                UserRoleAssignment(
                    user_id=user.id,
                    tenant_id=tenant.id,
                    role_id=role.id,
                    revoked_at=datetime.now(timezone.utc) if revoked else None,
                )
            )
            db_session.flush()

        elif role_slug is not None:
            role = (
                db_session.query(Role)
                .filter(Role.tenant_id.is_(None), Role.slug == role_slug)
                .one()
            )
            db_session.add(
                UserRoleAssignment(
                    user_id=user.id,
                    tenant_id=tenant.id,
                    role_id=role.id,
                    revoked_at=datetime.now(timezone.utc) if revoked else None,
                )
            )
            db_session.flush()

        token = create_access_token(user_id=user.id, tenant_id=tenant.id)
        return user, token

    return make


@pytest.fixture
def authorization_context_factory(db_session: Session):
    """Factory fixture building a real AuthorizationContext for a user.

    Delegates to the real AuthorizationRepository/AuthorizationService
    chain (no mocking) so service-level tests can obtain a context without
    going through an HTTP request, exactly mirroring what
    get_authorization_context does per-request in production.
    """

    def make(user: User, *, is_platform_tenant: bool = False):
        authorization_service = AuthorizationService(AuthorizationRepository(db_session))
        return authorization_service.build_context(
            user_id=user.id,
            tenant_id=user.tenant_id,
            is_platform_tenant=is_platform_tenant,
        )

    return make
