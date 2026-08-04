"""Sanitize provider-facing errors before persistence or API exposure.

Never returns raw exception text, tokens, credentials, request bodies,
stack traces, or provider JSON. Classification may inspect raw text only
to choose among a fixed set of safe messages.
"""

from __future__ import annotations

from app.core.exceptions import (
    CredentialDecryptionError,
    ProviderConnectionNotFoundError,
)

PROVIDER_REQUEST_FAILED = "Provider request failed."
AUTHENTICATION_FAILED = "Authentication with provider failed."
RATE_LIMIT_EXCEEDED = "Provider rate limit exceeded."
TEMPORARY_PROVIDER_ERROR = "Temporary provider error."
UNEXPECTED_PROVIDER_ERROR = "Unexpected provider error."

_SAFE_MESSAGES: frozenset[str] = frozenset(
    {
        PROVIDER_REQUEST_FAILED,
        AUTHENTICATION_FAILED,
        RATE_LIMIT_EXCEEDED,
        TEMPORARY_PROVIDER_ERROR,
        UNEXPECTED_PROVIDER_ERROR,
    }
)

_AUTH_KEYWORDS: tuple[str, ...] = (
    "unauthorized",
    "authentication",
    "authenticate",
    "access denied",
    "forbidden",
    "invalid token",
    "invalid_token",
    "expired token",
    "www-authenticate",
    " 401",
    "401 ",
    " 403",
    "403 ",
)

_RATE_KEYWORDS: tuple[str, ...] = (
    "rate limit",
    "ratelimit",
    "too many requests",
    "throttle",
    " 429",
    "429 ",
)

_TEMP_KEYWORDS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "temporarily unavailable",
    "service unavailable",
    "connection reset",
    "connection refused",
    " 502",
    "502 ",
    " 503",
    "503 ",
    " 504",
    "504 ",
)

_SENSITIVE_MARKERS: tuple[str, ...] = (
    "authorization:",
    "bearer ",
    "access_token",
    "access-token",
    "refresh_token",
    "refresh-token",
    "client_secret",
    "client-secret",
    "api_key",
    "api-key",
    "apikey",
    "encryption_key",
    "encryption-key",
    "private_key",
    "cookie:",
    "set-cookie",
    "postgresql://",
    "postgres://",
    "mysql://",
    "mongodb://",
    "redis://",
    "eyj",  # JWT header prefix (base64url of {"...)
    "traceback",
    "stack trace",
    "-----begin",
)

_TEMP_EXCEPTION_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    ConnectionResetError,
    ConnectionRefusedError,
    BrokenPipeError,
    InterruptedError,
)


def sanitize_provider_exception(exc: BaseException) -> str:
    """Map a provider-boundary exception to a safe generic message."""
    if isinstance(exc, CredentialDecryptionError):
        return AUTHENTICATION_FAILED
    if isinstance(exc, ProviderConnectionNotFoundError):
        return AUTHENTICATION_FAILED
    if isinstance(exc, _TEMP_EXCEPTION_TYPES):
        return TEMPORARY_PROVIDER_ERROR
    text = str(exc)
    if not text.strip():
        return UNEXPECTED_PROVIDER_ERROR
    return _classify_text(text)


def sanitize_provider_message(raw_message: str | None) -> str:
    """Map opaque provider/adapter error text to a safe generic message."""
    if raw_message is None or not str(raw_message).strip():
        return UNEXPECTED_PROVIDER_ERROR
    return _classify_text(str(raw_message))


def is_safe_provider_message(message: str) -> bool:
    """Return True if message is one of the allowlisted safe strings."""
    return message in _SAFE_MESSAGES


def _classify_text(text: str) -> str:
    lower = text.lower()
    sensitive = _contains_any(lower, _SENSITIVE_MARKERS) or _looks_like_stack(text)
    looks_json = _looks_like_json(text)

    if _contains_any(lower, _RATE_KEYWORDS):
        return RATE_LIMIT_EXCEEDED
    if _contains_any(lower, _AUTH_KEYWORDS):
        return AUTHENTICATION_FAILED
    if _contains_any(lower, _TEMP_KEYWORDS):
        return TEMPORARY_PROVIDER_ERROR

    if sensitive or looks_json:
        return UNEXPECTED_PROVIDER_ERROR

    return PROVIDER_REQUEST_FAILED


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _looks_like_stack(text: str) -> bool:
    return "Traceback (most recent call last)" in text or "\n  File \"" in text


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped[0] in "{[" and stripped[-1] in "}]":
        return True
    return '"error"' in stripped.lower() and "{" in stripped
