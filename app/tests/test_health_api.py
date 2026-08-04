"""Integration tests for /api/v1/health/* (public, no sleeps)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_health_service
from app.core.settings import settings
from app.main import app
from app.services.health_service import HealthService

LIVE_URL = "/api/v1/health/live"
READY_URL = "/api/v1/health/ready"
DATABASE_URL_PATH = "/api/v1/health/database"
WORKER_URL = "/api/v1/health/worker"

_SECRET_MARKERS = (
    "DATABASE_URL=",
    "postgresql+",
    "postgresql://",
    "password=",
    "super-secret",
    "jwt_secret",
)


class _FailingHealthService(HealthService):
    """HealthService that always fails database checks with a secretful error."""

    def __init__(self) -> None:
        super().__init__(session=None)

    def check_database(self) -> bool:
        try:
            raise RuntimeError(
                f"could not connect using DATABASE_URL={settings.database_url} "
                "password=super-secret-db-password"
            )
        except Exception:
            return False


@pytest.fixture
def failing_health_client(db_session: Session) -> Iterator[TestClient]:
    """TestClient with HealthService forced to report DB failure."""
    del db_session

    def _override() -> HealthService:
        return _FailingHealthService()

    app.dependency_overrides[get_health_service] = _override
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_health_service, None)


def _assert_no_secret_leakage(payload: Any) -> None:
    text = str(payload).lower()
    for marker in _SECRET_MARKERS:
        assert marker.lower() not in text


def test_live_always_returns_200_unauthenticated(client: TestClient) -> None:
    response = client.get(LIVE_URL)
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_ready_success(client: TestClient) -> None:
    response = client.get(READY_URL)
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_database_failure(failing_health_client: TestClient) -> None:
    response = failing_health_client.get(READY_URL)
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    _assert_no_secret_leakage(response.json())
    _assert_no_secret_leakage(response.text)


def test_database_endpoint_success(client: TestClient) -> None:
    response = client.get(DATABASE_URL_PATH)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_database_endpoint_failure(failing_health_client: TestClient) -> None:
    response = failing_health_client.get(DATABASE_URL_PATH)
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    _assert_no_secret_leakage(response.json())
    _assert_no_secret_leakage(response.text)


def test_worker_endpoint(client: TestClient) -> None:
    response = client.get(WORKER_URL)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"status"}
    assert body["status"] in {"available", "unavailable"}


def test_worker_unavailable_when_database_url_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "database_url", None)
    response = client.get(WORKER_URL)
    assert response.status_code == 200
    assert response.json() == {"status": "unavailable"}


def test_all_health_endpoints_allow_unauthenticated_access(
    client: TestClient,
) -> None:
    for url in (LIVE_URL, READY_URL, DATABASE_URL_PATH, WORKER_URL):
        response = client.get(url)
        assert response.status_code in {200, 503}, url
        assert response.status_code != 401, url
        assert response.status_code != 403, url


def test_response_schemas_are_exact(client: TestClient) -> None:
    live = client.get(LIVE_URL).json()
    assert live == {"status": "alive"}

    ready = client.get(READY_URL)
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    database = client.get(DATABASE_URL_PATH)
    assert database.status_code == 200
    assert database.json() == {"status": "ok"}

    worker = client.get(WORKER_URL).json()
    assert set(worker.keys()) == {"status"}


def test_live_does_not_require_health_service_dependency(
    client: TestClient,
) -> None:
    """Liveness must not depend on get_health_service / get_db."""

    def _boom() -> HealthService:
        raise AssertionError("live must not resolve get_health_service")

    app.dependency_overrides[get_health_service] = _boom
    try:
        response = client.get(LIVE_URL)
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}
    finally:
        app.dependency_overrides.pop(get_health_service, None)


def test_success_responses_do_not_leak_secrets(client: TestClient) -> None:
    for url in (LIVE_URL, READY_URL, DATABASE_URL_PATH, WORKER_URL):
        response = client.get(url)
        _assert_no_secret_leakage(response.json())
        _assert_no_secret_leakage(dict(response.headers))
