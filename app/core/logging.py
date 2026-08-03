"""Application logging configuration (Observability Foundation Phase 1).

Stdlib logging only: JSON or text formatters, contextvars-backed fields,
and configure_logging(). No third-party logging libraries. Formatters
must never raise -- logging must not break application execution.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.core.logging_context import get_context
from app.core.settings import settings

_HANDLER_NAME = "verisure-observability"

logger = logging.getLogger("verisure")


def _resolve_log_format() -> str:
    """Return 'json' or 'text' from settings, with env-based default."""
    if settings.log_format is not None:
        return settings.log_format
    env = (settings.app_env or "").strip().lower()
    if env in {"production", "prod", "staging"}:
        return "json"
    return "text"


def _resolve_log_level() -> int:
    """Map settings.log_level to a logging level; unknown names -> INFO."""
    name = (settings.log_level or "INFO").strip().upper()
    return logging.getLevelNamesMapping().get(name, logging.INFO)


def _json_safe(value: Any) -> Any:
    """Convert a value into something json.dumps can emit without error."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError, OverflowError):
        return str(value)


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per log line; never raises to callers."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            payload: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "service": settings.log_service_name,
            }
            for key, value in get_context().items():
                payload[key] = _json_safe(value)
            if record.exc_info:
                payload["exc_info"] = self.formatException(record.exc_info)
            return json.dumps(payload, default=str, ensure_ascii=False)
        except Exception:
            try:
                return json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "level": "ERROR",
                        "logger": "verisure.logging",
                        "message": "log formatting failed",
                        "service": settings.log_service_name,
                    },
                    default=str,
                )
            except Exception:
                return (
                    '{"level":"ERROR","message":"log formatting failed",'
                    '"logger":"verisure.logging"}'
                )


class TextLogFormatter(logging.Formatter):
    """Human-readable formatter that appends bound context fields."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )

    def format(self, record: logging.LogRecord) -> str:
        try:
            base = super().format(record)
            context = get_context()
            if not context:
                return base
            parts = [f"{key}={_json_safe(value)!s}" for key, value in context.items()]
            return f"{base} | {' '.join(parts)}"
        except Exception:
            try:
                return super().format(record)
            except Exception:
                return f"{record.levelname} | {record.name} | log formatting failed"


def configure_logging() -> None:
    """Configure stdlib logging for the API/worker processes.

    Idempotent for our named handler: replaces any previous
    verisure-observability handler on the root logger. Safe to call from
    app import and from tests.
    """
    level = _resolve_log_level()
    log_format = _resolve_log_format()
    formatter: logging.Formatter
    if log_format == "json":
        formatter = JsonLogFormatter()
    else:
        formatter = TextLogFormatter()

    root = logging.getLogger()
    root.setLevel(level)

    for existing in list(root.handlers):
        if existing.get_name() == _HANDLER_NAME:
            root.removeHandler(existing)
            existing.close()

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.set_name(_HANDLER_NAME)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    logger.setLevel(level)
