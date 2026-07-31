"""Argon2id password hashing utilities via pwdlib."""

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id."""
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return True if the plaintext password matches the stored hash."""
    return _password_hash.verify(password, password_hash)
