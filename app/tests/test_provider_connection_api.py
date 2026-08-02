"""API tests for provider-connection list / get / disconnect.

PHASE 5 SCOPE: this only tests the HTTP + dependency-injection wiring
added on top of the already-built ProviderConnectionService -- no new
domain logic is under test here (see
test_provider_connection_service.py for that). Every route enforces
authentication only (Depends(get_current_user)); there is no local
RBAC check. There is intentionally no connect / credential-upload /
OAuth endpoint yet -- fixtures create connections via the service layer
directly, using the same encryption key the API dependency override
installs for the request path.
"""

from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_credential_encryption_service
from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService
from app.main import app
from app.repositories.provider_connection_repository import (
    ProviderConnectionRepository,
)
from app.services.provider_connection_service import ProviderConnectionService

CONNECTIONS_URL = "/api/v1/provider-connections"
_NIL_UUID = "00000000-0000-0000-0000-000000000000"
_EXPECTED_KEYS = {
    "id",
    "provider",
    "external_account_id",
    "display_name",
    "credentials_expire_at",
    "status",
    "disconnected_at",
    "created_at",
    "updated_at",
}
_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def encryption_service() -> CredentialEncryptionService:
    return CredentialEncryptionService(_TEST_ENCRYPTION_KEY)


@pytest.fixture(autouse=True)
def _override_encryption_service(
    encryption_service: CredentialEncryptionService,
) -> Iterator[None]:
    """Install a working CredentialEncryptionService for the API path.

    ENCRYPTION_KEY is optional in Settings (None/blank fails closed), so
    tests must not depend on a real key being present in the local .env.
    The override uses the same key the fixture helpers encrypt with.
    """
    app.dependency_overrides[get_credential_encryption_service] = (
        lambda: encryption_service
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_credential_encryption_service, None)


def _make_connection_service(
    db_session: Session, encryption_service: CredentialEncryptionService
) -> ProviderConnectionService:
    return ProviderConnectionService(
        ProviderConnectionRepository(db_session), encryption_service, db_session
    )


def _create_connection(
    db_session: Session,
    encryption_service: CredentialEncryptionService,
    *,
    tenant_id,
    provider: Provider = Provider.META,
    credential_payload: bytes = b"opaque-test-credential",
    external_account_id: str | None = "acct-test-1",
    display_name: str | None = "Test Ad Account",
):
    service = _make_connection_service(db_session, encryption_service)
    return service.connect(
        tenant_id=tenant_id,
        provider=provider,
        credential_payload=credential_payload,
        external_account_id=external_account_id,
        display_name=display_name,
    )


# --- Unauthenticated ---------------------------------------------------------


def test_list_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.get(CONNECTIONS_URL)
    assert response.status_code == 401


def test_get_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.get(f"{CONNECTIONS_URL}/{_NIL_UUID}")
    assert response.status_code == 401


def test_disconnect_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.post(f"{CONNECTIONS_URL}/{_NIL_UUID}/disconnect")
    assert response.status_code == 401


# --- List --------------------------------------------------------------------


def test_list_returns_only_tenant_rows(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user_a, token_a = auth_fixture()
    user_b, _ = auth_fixture()
    own = _create_connection(
        db_session, encryption_service, tenant_id=user_a.tenant_id, provider=Provider.META
    )
    _create_connection(
        db_session,
        encryption_service,
        tenant_id=user_b.tenant_id,
        provider=Provider.META,
    )

    response = client.get(CONNECTIONS_URL, headers=_auth_headers(token_a))

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [str(own.id)]


# --- Get ---------------------------------------------------------------------


def test_get_returns_metadata(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user, token = auth_fixture()
    connection = _create_connection(
        db_session,
        encryption_service,
        tenant_id=user.tenant_id,
        provider=Provider.GOOGLE,
        external_account_id="acct-google-9",
        display_name="Google Ads",
    )

    response = client.get(
        f"{CONNECTIONS_URL}/{connection.id}", headers=_auth_headers(token)
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == _EXPECTED_KEYS
    assert body["id"] == str(connection.id)
    assert body["provider"] == "google"
    assert body["external_account_id"] == "acct-google-9"
    assert body["display_name"] == "Google Ads"
    assert body["status"] == "connected"
    assert body["disconnected_at"] is None
    assert "encrypted_credentials" not in body


def test_cross_tenant_get_returns_404(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user_a, _ = auth_fixture()
    _, token_b = auth_fixture()
    connection = _create_connection(
        db_session, encryption_service, tenant_id=user_a.tenant_id
    )

    response = client.get(
        f"{CONNECTIONS_URL}/{connection.id}", headers=_auth_headers(token_b)
    )

    assert response.status_code == 404


# --- Disconnect --------------------------------------------------------------


def test_disconnect_happy_path(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user, token = auth_fixture()
    connection = _create_connection(
        db_session, encryption_service, tenant_id=user.tenant_id
    )

    response = client.post(
        f"{CONNECTIONS_URL}/{connection.id}/disconnect",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == _EXPECTED_KEYS
    assert body["status"] == "disconnected"
    assert body["disconnected_at"] is not None
    assert "encrypted_credentials" not in body


def test_second_disconnect_returns_409(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user, token = auth_fixture()
    connection = _create_connection(
        db_session, encryption_service, tenant_id=user.tenant_id
    )
    first = client.post(
        f"{CONNECTIONS_URL}/{connection.id}/disconnect",
        headers=_auth_headers(token),
    )
    assert first.status_code == 200

    second = client.post(
        f"{CONNECTIONS_URL}/{connection.id}/disconnect",
        headers=_auth_headers(token),
    )

    assert second.status_code == 409


def test_cross_tenant_disconnect_returns_404(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user_a, _ = auth_fixture()
    _, token_b = auth_fixture()
    connection = _create_connection(
        db_session, encryption_service, tenant_id=user_a.tenant_id
    )

    response = client.post(
        f"{CONNECTIONS_URL}/{connection.id}/disconnect",
        headers=_auth_headers(token_b),
    )

    assert response.status_code == 404


def test_disconnected_row_reflected_correctly_on_get(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user, token = auth_fixture()
    connection = _create_connection(
        db_session, encryption_service, tenant_id=user.tenant_id
    )
    disconnect_response = client.post(
        f"{CONNECTIONS_URL}/{connection.id}/disconnect",
        headers=_auth_headers(token),
    )
    assert disconnect_response.status_code == 200

    get_response = client.get(
        f"{CONNECTIONS_URL}/{connection.id}", headers=_auth_headers(token)
    )

    assert get_response.status_code == 200
    body = get_response.json()
    assert body["status"] == "disconnected"
    assert body["disconnected_at"] is not None
    assert "encrypted_credentials" not in body


# --- Schema / sanitization ---------------------------------------------------


def test_encrypted_credentials_never_appears_in_list_or_get(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user, token = auth_fixture()
    connection = _create_connection(
        db_session, encryption_service, tenant_id=user.tenant_id
    )

    list_response = client.get(CONNECTIONS_URL, headers=_auth_headers(token))
    get_response = client.get(
        f"{CONNECTIONS_URL}/{connection.id}", headers=_auth_headers(token)
    )

    assert list_response.status_code == 200
    assert get_response.status_code == 200
    assert "encrypted_credentials" not in list_response.text
    assert "encrypted_credentials" not in get_response.text
    for item in list_response.json()["items"]:
        assert set(item.keys()) == _EXPECTED_KEYS
        assert "encrypted_credentials" not in item


def test_unexpected_disconnect_request_fields_rejected(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user, token = auth_fixture()
    connection = _create_connection(
        db_session, encryption_service, tenant_id=user.tenant_id
    )

    response = client.post(
        f"{CONNECTIONS_URL}/{connection.id}/disconnect",
        headers=_auth_headers(token),
        json={"unexpected_field": "value"},
    )

    assert response.status_code == 422


def test_response_keys_exactly_match_schema(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user, token = auth_fixture()
    connection = _create_connection(
        db_session, encryption_service, tenant_id=user.tenant_id
    )

    response = client.get(
        f"{CONNECTIONS_URL}/{connection.id}", headers=_auth_headers(token)
    )

    assert response.status_code == 200
    assert set(response.json().keys()) == _EXPECTED_KEYS
