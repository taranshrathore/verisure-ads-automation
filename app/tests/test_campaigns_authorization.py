"""End-to-end authorization tests for GET /api/v1/campaigns.

Covers the full JWT -> CurrentUser -> AuthorizationContext ->
permission-resolution -> endpoint flow, exercised against the real
database via the authorization_fixture in conftest.py. No mocking, no
dependency overrides.
"""

from fastapi.testclient import TestClient

CAMPAIGNS_URL = "/api/v1/campaigns"


def test_unauthenticated_request_returns_401(client: TestClient) -> None:
    """A request with no bearer token is rejected before any DB lookup."""
    response = client.get(CAMPAIGNS_URL)

    assert response.status_code == 401


def test_authenticated_user_without_campaigns_read_returns_403(
    client: TestClient, authorization_fixture
) -> None:
    """A valid, active user with zero role assignments lacks the permission."""
    _, token = authorization_fixture()

    response = client.get(
        CAMPAIGNS_URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403


def test_authenticated_user_with_campaigns_read_succeeds(
    client: TestClient, authorization_fixture
) -> None:
    """An active assignment of the built-in viewer role grants campaigns:read."""
    _, token = authorization_fixture(role_slug="viewer")

    response = client.get(
        CAMPAIGNS_URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_super_admin_succeeds_through_has_permission_bypass(
    client: TestClient, authorization_fixture
) -> None:
    """A platform-tenant super_admin passes despite holding zero tenant permissions."""
    _, token = authorization_fixture(system_role="super_admin")

    response = client.get(
        CAMPAIGNS_URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_revoked_role_assignment_no_longer_grants_access(
    client: TestClient, authorization_fixture
) -> None:
    """A revoked assignment of a permission-granting role no longer authorizes."""
    _, token = authorization_fixture(role_slug="viewer", revoked=True)

    response = client.get(
        CAMPAIGNS_URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403


def test_soft_deleted_role_no_longer_grants_access(
    client: TestClient, authorization_fixture
) -> None:
    """An active assignment of a soft-deleted role no longer authorizes."""
    _, token = authorization_fixture(grant_custom_role=True, soft_delete_role=True)

    response = client.get(
        CAMPAIGNS_URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403
