"""Opaque refresh-token generation and hashing utilities."""

import hashlib
import secrets


def generate_refresh_token() -> str:
    """Generate a cryptographically random opaque refresh token."""
    return secrets.token_urlsafe(32)


def hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest of a raw refresh token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
