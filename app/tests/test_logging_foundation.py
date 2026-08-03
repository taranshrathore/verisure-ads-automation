"""Unit tests for Observability Foundation Phase 1 (logging core).

No middleware, service logs, worker logs, or request-ID HTTP behavior.
"""

from __future__ import annotations

import json
import logging
from uuid import uuid4

import pytest

from app.core import logging as logging_module
from app.core.logging import (
    JsonLogFormatter,
    TextLogFormatter,
    _json_safe,
    configure_logging,
)
from app.core.logging_context import (
    bind,
    bound_context,
    clear,
    get_context,
    reset,
)
from app.core.settings import Settings


@pytest.fixture(autouse=True)
def _clean_log_context() -> None:
    clear()
    yield
    clear()


class _NotJsonSerializable:
    def __str__(self) -> str:
        return "not-json-object"


def test_json_safe_converts_non_serializable_to_str() -> None:
    assert _json_safe(_NotJsonSerializable()) == "not-json-object"


def test_json_formatter_never_raises_on_non_serializable_context() -> None:
    formatter = JsonLogFormatter()
    bind(weird=_NotJsonSerializable(), job_id=uuid4())
    record = logging.LogRecord(
        name="verisure.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    rendered = formatter.format(record)
    payload = json.loads(rendered)
    assert payload["message"] == "hello"
    assert payload["weird"] == "not-json-object"
    assert "job_id" in payload


def test_json_formatter_never_raises_when_message_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="verisure.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ok",
        args=(),
        exc_info=None,
    )

    def _boom() -> str:
        raise RuntimeError("getMessage failed")

    monkeypatch.setattr(record, "getMessage", _boom)
    rendered = formatter.format(record)
    payload = json.loads(rendered)
    assert payload["message"] == "log formatting failed"
    assert payload["level"] == "ERROR"


def test_text_formatter_includes_bound_context() -> None:
    formatter = TextLogFormatter()
    bind(request_id="abc-123")
    record = logging.LogRecord(
        name="verisure.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    rendered = formatter.format(record)
    assert "hello" in rendered
    assert "request_id=abc-123" in rendered


def test_bound_context_cleared_after_exception() -> None:
    clear()
    with pytest.raises(RuntimeError, match="boom"):
        with bound_context(job_id="job-1", tenant_id="tenant-1"):
            assert get_context()["job_id"] == "job-1"
            raise RuntimeError("boom")
    assert get_context() == {}


def test_bind_clear_in_finally_cleared_after_exception() -> None:
    clear()
    token = bind(campaign_id="camp-1")
    try:
        assert get_context()["campaign_id"] == "camp-1"
        raise RuntimeError("boom")
    except RuntimeError:
        reset(token)
    assert get_context() == {}


def test_nested_bound_context_restores_outer() -> None:
    clear()
    with bound_context(request_id="outer"):
        assert get_context() == {"request_id": "outer"}
        with bound_context(job_id="inner"):
            assert get_context()["request_id"] == "outer"
            assert get_context()["job_id"] == "inner"
        assert get_context() == {"request_id": "outer"}
    assert get_context() == {}


def test_configure_logging_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging_module.settings, "log_format", "json")
    monkeypatch.setattr(logging_module.settings, "log_level", "INFO")
    monkeypatch.setattr(logging_module.settings, "log_service_name", "verisure-test")
    configure_logging()

    root = logging.getLogger()
    handler = next(
        h for h in root.handlers if h.get_name() == "verisure-observability"
    )
    assert isinstance(handler.formatter, JsonLogFormatter)


def test_configure_logging_text_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging_module.settings, "log_format", "text")
    configure_logging()
    root = logging.getLogger()
    handler = next(
        h for h in root.handlers if h.get_name() == "verisure-observability"
    )
    assert isinstance(handler.formatter, TextLogFormatter)


def test_configure_logging_defaults_json_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging_module.settings, "log_format", None)
    monkeypatch.setattr(logging_module.settings, "app_env", "production")
    configure_logging()
    root = logging.getLogger()
    handler = next(
        h for h in root.handlers if h.get_name() == "verisure-observability"
    )
    assert isinstance(handler.formatter, JsonLogFormatter)


def test_log_format_setting_rejects_invalid_value() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(
            jwt_secret_key="test-secret-key-for-settings-validation",
            log_format="xml",  # type: ignore[arg-type]
        )


def test_context_fields_appear_in_json_log_output() -> None:
    formatter = JsonLogFormatter()
    with bound_context(
        request_id="req-1",
        job_id="job-1",
        tenant_id="tenant-1",
        campaign_id="camp-1",
    ):
        record = logging.LogRecord(
            name="verisure.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="enqueued",
            args=(),
            exc_info=None,
        )
        payload = json.loads(formatter.format(record))
    assert payload["request_id"] == "req-1"
    assert payload["job_id"] == "job-1"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["campaign_id"] == "camp-1"
    assert get_context() == {}
