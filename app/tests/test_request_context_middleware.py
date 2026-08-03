"""Tests for Observability Foundation Phase 2 request-context middleware."""

from __future__ import annotations

import logging
from uuid import UUID

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.logging_context import clear, get_context
from app.main import app as fastapi_app
from app.middleware.request_context import (
    RequestContextMiddleware,
    resolve_request_id,
)


@pytest.fixture(autouse=True)
def _clean_context() -> None:
    clear()
    yield
    clear()


def _ok(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def _boom(_request: Request) -> PlainTextResponse:
    raise RuntimeError("boom")


def _context_probe(_request: Request) -> PlainTextResponse:
    return PlainTextResponse(get_context().get("request_id", ""))


def _build_client(*routes: Route) -> TestClient:
    inner = Starlette(routes=list(routes))
    wrapped = RequestContextMiddleware(inner)
    return TestClient(wrapped, raise_server_exceptions=True)


def test_middleware_is_pure_asgi_not_base_http_middleware() -> None:
    assert not issubclass(RequestContextMiddleware, BaseHTTPMiddleware)
    assert callable(RequestContextMiddleware(Starlette()).__call__)


def test_resolve_request_id_generates_uuid_when_missing() -> None:
    value = resolve_request_id(None)
    UUID(value)  # raises if not a UUID


def test_resolve_request_id_reuses_valid_inbound() -> None:
    assert resolve_request_id("client-corr-id-123") == "client-corr-id-123"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "x" * 129,
        "bad\nvalue",
        "bad\rvalue",
        "has\x00null",
    ],
)
def test_resolve_request_id_rejects_unsafe_values(raw: str) -> None:
    value = resolve_request_id(raw)
    assert value != raw.strip()
    UUID(value)


def test_generated_request_id_echoed_on_response() -> None:
    client = _build_client(Route("/", _ok))
    response = client.get("/")
    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    UUID(request_id)


def test_valid_inbound_request_id_reused_and_echoed() -> None:
    client = _build_client(Route("/", _ok))
    response = client.get("/", headers={"X-Request-ID": "trace-abc-001"})
    assert response.headers["x-request-id"] == "trace-abc-001"


def test_invalid_request_id_replaced_and_echoed() -> None:
    client = _build_client(Route("/", _ok))
    response = client.get("/", headers={"X-Request-ID": "bad\ninject"})
    echoed = response.headers["x-request-id"]
    assert echoed != "bad\ninject"
    UUID(echoed)


def test_context_cleared_after_success() -> None:
    client = _build_client(Route("/probe", _context_probe))
    response = client.get("/probe")
    assert response.status_code == 200
    assert response.text != ""
    assert get_context() == {}


def test_context_cleared_after_exception() -> None:
    client = _build_client(Route("/boom", _boom))
    with pytest.raises(RuntimeError, match="boom"):
        client.get("/boom")
    assert get_context() == {}


def test_exception_propagation_unchanged() -> None:
    client = _build_client(Route("/boom", _boom))
    with pytest.raises(RuntimeError, match="boom"):
        client.get("/boom")


def test_access_log_emitted_once(caplog: pytest.LogCaptureFixture) -> None:
    client = _build_client(Route("/widgets", _ok))
    with caplog.at_level(logging.INFO, logger="verisure.access"):
        response = client.get("/widgets")
    assert response.status_code == 200
    access_records = [
        r for r in caplog.records if r.name == "verisure.access" and r.message == "http_request"
    ]
    assert len(access_records) == 1


def test_health_is_not_logged_at_info(caplog: pytest.LogCaptureFixture) -> None:
    client = _build_client(Route("/health", _ok))
    with caplog.at_level(logging.DEBUG, logger="verisure.access"):
        response = client.get("/health")
    assert response.status_code == 200
    info_records = [
        r
        for r in caplog.records
        if r.name == "verisure.access"
        and r.message == "http_request"
        and r.levelno >= logging.INFO
    ]
    assert info_records == []
    debug_records = [
        r
        for r in caplog.records
        if r.name == "verisure.access"
        and r.message == "http_request"
        and r.levelno == logging.DEBUG
    ]
    assert len(debug_records) == 1


def test_fastapi_app_echoes_request_id_header() -> None:
    """Integration: middleware is registered on the real FastAPI app."""
    with TestClient(fastapi_app) as client:
        response = client.get("/health", headers={"X-Request-ID": "health-trace-1"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "health-trace-1"
    assert get_context() == {}
