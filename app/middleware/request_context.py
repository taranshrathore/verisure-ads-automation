"""ASGI request-context middleware (Observability Foundation Phase 2).

Assigns a safe request_id, binds it into logging context for the request,
echoes X-Request-ID, emits a single access log, and always clears context.
Does not use BaseHTTPMiddleware. Does not log headers, bodies, or secrets.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging_context import bind, clear

logger = logging.getLogger("verisure.access")

_MAX_REQUEST_ID_LENGTH = 128
_QUIET_ACCESS_PATHS = frozenset({"/health"})


def resolve_request_id(raw: str | None) -> str:
    """Accept a safe inbound X-Request-ID, or generate a UUID4.

    Rejects blank, overlong, non-printable, or CR/LF-containing values so
    request headers cannot inject into logs.
    """
    if raw is None:
        return str(uuid4())
    candidate = raw.strip()
    if not candidate:
        return str(uuid4())
    if len(candidate) > _MAX_REQUEST_ID_LENGTH:
        return str(uuid4())
    if "\r" in candidate or "\n" in candidate:
        return str(uuid4())
    if not candidate.isprintable():
        return str(uuid4())
    return candidate


class RequestContextMiddleware:
    """Pure ASGI middleware: request_id + access log + context cleanup."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = resolve_request_id(headers.get("x-request-id"))
        method = scope.get("method", "")
        path = scope.get("path", "")
        client = scope.get("client")
        remote_addr = client[0] if client else None

        status_code_holder = {"status": 500}
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code_holder["status"] = message["status"]
                mutable = MutableHeaders(scope=message)
                mutable["X-Request-ID"] = request_id
            await send(message)

        bind(request_id=request_id)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                duration_ms = int((time.perf_counter() - started) * 1000)
                bind(
                    method=method,
                    path=path,
                    status=status_code_holder["status"],
                    duration_ms=duration_ms,
                    service="api",
                )
                if remote_addr is not None:
                    bind(remote_addr=remote_addr)
                if path in _QUIET_ACCESS_PATHS:
                    logger.debug("http_request")
                else:
                    logger.info("http_request")
            finally:
                clear()
