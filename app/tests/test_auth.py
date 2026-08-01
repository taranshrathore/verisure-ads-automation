"""Authentication-flow tests: login, invalid credentials, and tenant-scoped
identity resolution.

TEMPORARY / CRM-MIGRATION STATE: no role/permission checks exist in this
backend anymore -- see docs/HANDOFF.md. These tests exercise
authentication only (JWT issuance/validation and tenant/user active-state
checks), against the real database via auth_fixture in conftest.py. No
mocking, no dependency overrides beyond get_db.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security.jwt import create_access_token
from app.core.security.password import hash_password
from app.models.tenant import Tenant
from app.models.user import User

LOGIN_URL = "/api/v1/auth/login"
LOGOUT_ALL_URL = "/api/v1/auth/logout-all"


def test_login_succeeds_with_valid_credentials(
    client: TestClient, db_session: Session
) -> None:
    """A user with a real password hash can log in and receive a token pair."""
    tenant = Tenant(name="Login Test Tenant", slug="login-test-tenant")
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email="login-test@example.com",
        hashed_password=hash_password("correct-password"),
        role="member",
    )
    db_session.add(user)
    db_session.flush()

    response = client.post(
        LOGIN_URL,
        json={
            "tenant_slug": tenant.slug,
            "email": user.email,
            "password": "correct-password",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body


def test_login_rejects_wrong_password(client: TestClient, db_session: Session) -> None:
    """An incorrect password is rejected with a generic invalid-credentials error."""
    tenant = Tenant(name="Login Test Tenant 2", slug="login-test-tenant-2")
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email="login-test-2@example.com",
        hashed_password=hash_password("correct-password"),
        role="member",
    )
    db_session.add(user)
    db_session.flush()

    response = client.post(
        LOGIN_URL,
        json={
            "tenant_slug": tenant.slug,
            "email": user.email,
            "password": "wrong-password",
        },
    )

    assert response.status_code == 401


def test_unauthenticated_request_returns_401(client: TestClient) -> None:
    """A request with no bearer token is rejected before any DB lookup."""
    response = client.post(LOGOUT_ALL_URL, json={})

    assert response.status_code == 401


def test_soft_deleted_user_is_rejected_despite_valid_token(
    client: TestClient, auth_fixture
) -> None:
    """A structurally valid, unexpired token for a soft-deleted user is denied."""
    _, token = auth_fixture(user_deleted=True)

    response = client.post(
        LOGOUT_ALL_URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401


def test_soft_deleted_tenant_is_rejected_despite_valid_token(
    client: TestClient, auth_fixture
) -> None:
    """A structurally valid, unexpired token for a soft-deleted tenant is denied."""
    _, token = auth_fixture(tenant_deleted=True)

    response = client.post(
        LOGOUT_ALL_URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401


def test_tenant_isolation_rejects_token_whose_user_belongs_to_another_tenant(
    client: TestClient, db_session: Session, auth_fixture
) -> None:
    """A token asserting a (user_id, tenant_id) pair that doesn't match any
    row is rejected -- get_current_user's tenant-scoped lookup must never
    resolve a user under a tenant they don't actually belong to.
    """
    user, _ = auth_fixture()

    other_tenant = Tenant(name="Other Tenant", slug="other-isolation-tenant")
    db_session.add(other_tenant)
    db_session.flush()

    forged_token = create_access_token(user_id=user.id, tenant_id=other_tenant.id)

    response = client.post(
        LOGOUT_ALL_URL, headers={"Authorization": f"Bearer {forged_token}"}
    )

    assert response.status_code == 401
