"""Encryption service for provider credentials at rest.

Framework-agnostic: no SQLAlchemy, FastAPI, or Pydantic imports, and no
dependency on any provider-specific ORM model. The only non-stdlib
dependency is cryptography.fernet.MultiFernet; the only in-repo
dependency is app.core.providers.Provider, a plain StrEnum with no ORM
coupling of its own. This keeps the encryption boundary safely reusable
by any future caller (a ProviderConnectionService, a background job,
etc.) without pulling in the model/session layer.

Credential bytes are never stored or returned on their own. They are
first wrapped in a small JSON "envelope" that also records
envelope_version, tenant_id, and provider, and *that* envelope is what
gets Fernet-encrypted. Fernet authenticates ciphertext integrity (it
will reject anything tampered with), but it does not know or care which
database row a given ciphertext "belongs" to -- without the envelope, a
ciphertext value copied from a different tenant's or provider's row
would decrypt just fine. Binding tenant_id/provider into the encrypted
envelope and re-checking them against the caller's requested context on
every decrypt closes that gap.
"""

import base64
import binascii
import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.exceptions import (
    CredentialDecryptionError,
    CredentialEncryptionUnavailableError,
)
from app.core.providers import Provider

_ENVELOPE_VERSION = 1


class CredentialEncryptionService:
    """Encrypts/decrypts opaque provider-credential bytes using Fernet.

    Construct with the raw, comma-separated key string (typically
    settings.encryption_key) rather than reading settings internally, so
    this class stays a small, pure, independently constructible/testable
    unit with no hidden global state.

    Key rotation: the *first* key in the list is always used to encrypt;
    every key in the list is tried when decrypting (this is exactly what
    cryptography.fernet.MultiFernet does). To rotate keys: prepend a new
    key, keep the old key(s) in the list only until nothing on disk still
    needs them, then remove the old key(s) entirely.
    """

    def __init__(self, encryption_keys: str | None) -> None:
        keys = _parse_keys(encryption_keys)
        self._multi_fernet = MultiFernet([_build_fernet(key) for key in keys])

    def encrypt_credentials(
        self,
        tenant_id: UUID,
        provider: Provider,
        credential_payload: bytes,
    ) -> bytes:
        """Encrypt opaque credential bytes into context-bound ciphertext.

        credential_payload is treated as fully opaque -- this method does
        not parse, validate, or interpret it in any way beyond base64-
        encoding it for embedding in the JSON envelope.
        """
        envelope = {
            "envelope_version": _ENVELOPE_VERSION,
            "tenant_id": str(tenant_id),
            "provider": provider.value,
            "credential_payload": base64.b64encode(credential_payload).decode(
                "ascii"
            ),
        }
        envelope_bytes = json.dumps(envelope).encode("utf-8")
        return self._multi_fernet.encrypt(envelope_bytes)

    def decrypt_credentials(
        self,
        tenant_id: UUID,
        provider: Provider,
        ciphertext: bytes,
    ) -> bytes:
        """Decrypt ciphertext and return the original credential bytes.

        Fails closed with CredentialDecryptionError -- always with the
        same generic message, regardless of which check failed -- for any
        of: a key not present in this service's configured key list,
        tampered ciphertext, malformed envelope JSON/base64, an
        unsupported envelope version, or an envelope whose tenant_id/
        provider does not match the (tenant_id, provider) passed in here.
        Never distinguishing *why* avoids leaking information useful to
        an attacker probing stored ciphertext.
        """
        try:
            envelope_bytes = self._multi_fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise CredentialDecryptionError() from exc

        try:
            envelope = json.loads(envelope_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialDecryptionError() from exc

        if not isinstance(envelope, dict):
            raise CredentialDecryptionError()
        if envelope.get("envelope_version") != _ENVELOPE_VERSION:
            raise CredentialDecryptionError()
        if envelope.get("tenant_id") != str(tenant_id):
            raise CredentialDecryptionError()
        if envelope.get("provider") != provider.value:
            raise CredentialDecryptionError()

        encoded_payload = envelope.get("credential_payload")
        if not isinstance(encoded_payload, str):
            raise CredentialDecryptionError()

        try:
            return base64.b64decode(encoded_payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CredentialDecryptionError() from exc


def _parse_keys(encryption_keys: str | None) -> list[str]:
    """Split, trim, and validate a comma-separated key configuration string.

    Fails closed (CredentialEncryptionUnavailableError) rather than
    silently proceeding with fewer keys than configured, for: a missing
    value, a blank/whitespace-only value, and any individual comma-
    separated entry that is blank after trimming (e.g. a stray trailing
    comma).
    """
    if encryption_keys is None or not encryption_keys.strip():
        raise CredentialEncryptionUnavailableError(
            "Credential encryption is unavailable: no encryption key is configured."
        )

    keys: list[str] = []
    for raw_key in encryption_keys.split(","):
        key = raw_key.strip()
        if not key:
            raise CredentialEncryptionUnavailableError(
                "Credential encryption is unavailable: a configured key is blank."
            )
        keys.append(key)
    return keys


def _build_fernet(key: str) -> Fernet:
    """Construct a Fernet instance, failing closed on a malformed key.

    The underlying key material is never included in the resulting
    exception message.
    """
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise CredentialEncryptionUnavailableError(
            "Credential encryption is unavailable: a configured key is invalid."
        ) from exc
