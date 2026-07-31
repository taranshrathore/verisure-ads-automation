"""Endpoint-level tests for role management API routes.

Exercises the real JWT -> AuthorizationContext -> require_permission ->
RoleManagementService chain through the API, mirroring
test_campaigns_authorization.py's pattern. No mocking, no dependency
overrides beyond get_db (see conftest.py).
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security.jwt import create_access_token
from app.models.role import Role
from app.models.user import User
from app.models.user_role_assignment import UserRoleAssignment

ROLES_URL = "/api/v1/roles"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _builtin_role_id(db_session: Session, slug: str) -> uuid.UUID:
    return (
        db_session.query(Role)
        .filter(Role.tenant_id.is_(None), Role.slug == slug)
        .one()
        .id
    )


def test_list_roles_requires_authentication(client: TestClient) -> None:
    response = client.get(ROLES_URL)
    assert response.status_code == 401


def test_list_roles_requires_roles_read(client: TestClient, authorization_fixture) -> None:
    _, token = authorization_fixture()

    response = client.get(ROLES_URL, headers=_auth_headers(token))

    assert response.status_code == 403


def test_list_roles_succeeds_and_includes_builtins(
    client: TestClient, authorization_fixture
) -> None:
    _, token = authorization_fixture(role_slug="tenant_admin")

    response = client.get(ROLES_URL, headers=_auth_headers(token))

    assert response.status_code == 200
    slugs = {role["slug"] for role in response.json()}
    assert {"tenant_admin", "manager", "employee", "viewer"} <= slugs


def test_create_role_success(client: TestClient, authorization_fixture) -> None:
    _, token = authorization_fixture(role_slug="tenant_admin")

    response = client.post(
        ROLES_URL,
        headers=_auth_headers(token),
        json={
            "slug": "reporter",
            "name": "Reporter",
            "permission_slugs": ["campaigns:read"],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "reporter"
    assert body["is_builtin"] is False
    assert body["permission_slugs"] == ["campaigns:read"]


def test_create_role_rejects_reserved_slug(client: TestClient, authorization_fixture) -> None:
    _, token = authorization_fixture(role_slug="tenant_admin")

    response = client.post(
        ROLES_URL,
        headers=_auth_headers(token),
        json={"slug": "tenant_admin", "name": "Fake Admin", "permission_slugs": []},
    )

    assert response.status_code == 403


def test_create_role_denies_subset_delegation_violation(
    client: TestClient, authorization_fixture, db_session: Session
) -> None:
    """A caller holding roles:manage cannot grant a permission it lacks."""
    admin, admin_token = authorization_fixture(role_slug="tenant_admin")

    limited_role_response = client.post(
        ROLES_URL,
        headers=_auth_headers(admin_token),
        json={
            "slug": "limited-manager",
            "name": "Limited Manager",
            "permission_slugs": ["roles:manage", "campaigns:read"],
        },
    )
    assert limited_role_response.status_code == 201
    limited_role_id = limited_role_response.json()["id"]

    limited_user = User(
        tenant_id=admin.tenant_id,
        email=f"limited-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(limited_user)
    db_session.flush()

    assign_response = client.post(
        f"/api/v1/users/{limited_user.id}/roles",
        headers=_auth_headers(admin_token),
        json={"role_id": limited_role_id},
    )
    assert assign_response.status_code == 201

    limited_token = create_access_token(
        user_id=limited_user.id, tenant_id=admin.tenant_id
    )

    response = client.post(
        ROLES_URL,
        headers=_auth_headers(limited_token),
        json={
            "slug": "escalated",
            "name": "Escalated",
            "permission_slugs": ["campaigns:manage"],
        },
    )

    assert response.status_code == 403


def test_update_role_rejects_builtin(
    client: TestClient, authorization_fixture, db_session: Session
) -> None:
    _, token = authorization_fixture(role_slug="tenant_admin")
    role_id = _builtin_role_id(db_session, "viewer")

    response = client.patch(
        f"{ROLES_URL}/{role_id}",
        headers=_auth_headers(token),
        json={"name": "Hacked Viewer"},
    )

    assert response.status_code == 403


def test_delete_role_with_active_assignment_returns_409(
    client: TestClient, authorization_fixture
) -> None:
    admin, token = authorization_fixture(role_slug="tenant_admin")

    create_response = client.post(
        ROLES_URL,
        headers=_auth_headers(token),
        json={"slug": "reporter", "name": "Reporter", "permission_slugs": []},
    )
    role_id = create_response.json()["id"]

    assign_response = client.post(
        f"/api/v1/users/{admin.id}/roles",
        headers=_auth_headers(token),
        json={"role_id": role_id},
    )
    assert assign_response.status_code == 201

    response = client.delete(f"{ROLES_URL}/{role_id}", headers=_auth_headers(token))

    assert response.status_code == 409


def test_assign_role_to_cross_tenant_user_returns_404(
    client: TestClient, authorization_fixture
) -> None:
    admin, token = authorization_fixture(role_slug="tenant_admin")
    other_tenant_user, _ = authorization_fixture()

    roles_response = client.get(ROLES_URL, headers=_auth_headers(token))
    viewer_role_id = next(
        role["id"] for role in roles_response.json() if role["slug"] == "viewer"
    )

    response = client.post(
        f"/api/v1/users/{other_tenant_user.id}/roles",
        headers=_auth_headers(token),
        json={"role_id": viewer_role_id},
    )

    assert response.status_code == 404


def test_assign_and_revoke_role_assignment(
    client: TestClient, authorization_fixture
) -> None:
    admin, token = authorization_fixture(role_slug="tenant_admin")

    roles_response = client.get(ROLES_URL, headers=_auth_headers(token))
    viewer_role_id = next(
        role["id"] for role in roles_response.json() if role["slug"] == "viewer"
    )

    assign_response = client.post(
        f"/api/v1/users/{admin.id}/roles",
        headers=_auth_headers(token),
        json={"role_id": viewer_role_id},
    )
    assert assign_response.status_code == 201
    assignment_id = assign_response.json()["id"]

    revoke_response = client.delete(
        f"/api/v1/role-assignments/{assignment_id}", headers=_auth_headers(token)
    )

    assert revoke_response.status_code == 204


def test_revoke_last_tenant_admin_assignment_returns_409(
    client: TestClient, authorization_fixture, db_session: Session
) -> None:
    admin, token = authorization_fixture(role_slug="tenant_admin")
    admin_role_id = _builtin_role_id(db_session, "tenant_admin")
    assignment = (
        db_session.query(UserRoleAssignment)
        .filter(
            UserRoleAssignment.user_id == admin.id,
            UserRoleAssignment.role_id == admin_role_id,
        )
        .one()
    )

    response = client.delete(
        f"/api/v1/role-assignments/{assignment.id}", headers=_auth_headers(token)
    )

    assert response.status_code == 409
