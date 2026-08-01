"""Proves the local RBAC / role-management surface has actually been removed.

See docs/HANDOFF.md for the CRM migration status this backs.
"""

from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import Session

_DROPPED_RBAC_TABLES = (
    "permissions",
    "roles",
    "role_permissions",
    "user_role_assignments",
    "system_role_assignments",
)


def test_role_management_endpoints_no_longer_exist(
    client: TestClient, auth_fixture
) -> None:
    """/api/v1/roles is gone: no route registered, so FastAPI returns 404
    (not 401/403), for both authenticated and unauthenticated callers.
    """
    _, token = auth_fixture()
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/v1/roles", headers=headers).status_code == 404
    assert client.post("/api/v1/roles", json={}, headers=headers).status_code == 404
    assert client.get("/api/v1/roles").status_code == 404


def test_no_local_rbac_tables_required_by_app_startup(
    client: TestClient, db_session: Session
) -> None:
    """The app (already started by the `client` fixture above) and a real
    request against it must not depend on any of the dropped RBAC tables.
    Belt-and-suspenders: also assert those tables genuinely do not exist in
    the schema migrations left them in (proving this isn't passing merely
    because no code path happens to touch them).
    """
    response = client.get("/health")
    assert response.status_code == 200

    inspector = inspect(db_session.get_bind())
    existing_tables = set(inspector.get_table_names())
    for table_name in _DROPPED_RBAC_TABLES:
        assert table_name not in existing_tables, (
            f"{table_name} still exists; alembic upgrade head was not run "
            "against the test database, or the removal migration is broken."
        )
