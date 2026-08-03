"""Request/worker logging context via contextvars.

Observability Foundation Phase 1: bind/clear only. Middleware and
worker wiring bind concrete IDs in later phases. Every bind that is not
done through bound_context() must be paired with clear() (or reset)
inside a finally block so context cannot leak across requests or worker
iterations.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

_LOG_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "verisure_log_context", default=None
)


def get_context() -> dict[str, Any]:
    """Return a shallow copy of the current log context."""
    ctx = _LOG_CONTEXT.get()
    return dict(ctx) if ctx else {}


def bind(**fields: Any) -> Token[dict[str, Any] | None]:
    """Merge fields into the current log context.

    Returns the ContextVar token from set(). Prefer bound_context() so
    unbind is exception-safe; if calling bind() directly, always reset or
    clear() in a finally block.
    """
    merged = get_context()
    for key, value in fields.items():
        if value is not None:
            merged[key] = value
    return _LOG_CONTEXT.set(merged)


def clear() -> None:
    """Remove all bound log context fields."""
    _LOG_CONTEXT.set(None)


def reset(token: Token[dict[str, Any] | None]) -> None:
    """Restore context to the state captured by a prior bind() token."""
    _LOG_CONTEXT.reset(token)


@contextmanager
def bound_context(**fields: Any) -> Iterator[None]:
    """Bind fields for the duration of a block; always restore afterward."""
    token = bind(**fields)
    try:
        yield
    finally:
        reset(token)
