"""Pure unit tests for CredentialEncryptionService (Provider Account
Connection milestone, Phase 1).

No database, no ORM session, no HTTP: CredentialEncryptionService is a
plain in-memory object with no I/O beyond cryptography.fernet. The two
model-inspection tests at the bottom read declared SQLAlchemy table
metadata only -- they never open a database connection.
"""

import base64
import json
import uuid

import pytest
from cryptography.fernet import Fernet, MultiFernet

from app.core.exceptions import (
    CredentialDecryptionError,
    CredentialEncryptionUnavailableError,
)
from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService

KEY_A = Fernet.generate_key().decode("ascii")
KEY_B = Fernet.generate_key().decode("ascii")


def _service(*keys: str) -> CredentialEncryptionService:
    return CredentialEncryptionService(",".join(keys))


# --- Round-trip ----------------------------------------------------------------


def test_encrypt_then_decrypt_round_trips_credential_bytes() -> None:
    service = _service(KEY_A)
    tenant_id = uuid.uuid4()

    ciphertext = service.encrypt_credentials(
        tenant_id, Provider.META, b"super-secret-token"
    )
    plaintext = service.decrypt_credentials(tenant_id, Provider.META, ciphertext)

    assert plaintext == b"super-secret-token"


def test_round_trip_preserves_arbitrary_binary_payload() -> None:
    service = _service(KEY_A)
    tenant_id = uuid.uuid4()
    payload = bytes(range(256))

    ciphertext = service.encrypt_credentials(tenant_id, Provider.GOOGLE, payload)
    plaintext = service.decrypt_credentials(tenant_id, Provider.GOOGLE, ciphertext)

    assert plaintext == payload


def test_identical_plaintext_produces_different_ciphertext() -> None:
    service = _service(KEY_A)
    tenant_id = uuid.uuid4()

    first = service.encrypt_credentials(tenant_id, Provider.META, b"same-secret")
    second = service.encrypt_credentials(tenant_id, Provider.META, b"same-secret")

    assert first != second


# --- Key rotation ----------------------------------------------------------------


def test_encryption_always_uses_the_first_configured_key() -> None:
    service = _service(KEY_A, KEY_B)
    ciphertext = service.encrypt_credentials(
        uuid.uuid4(), Provider.META, b"rotation-check"
    )

    Fernet(KEY_A.encode("ascii")).decrypt(ciphertext)  # does not raise

    with pytest.raises(Exception):
        Fernet(KEY_B.encode("ascii")).decrypt(ciphertext)


def test_multifernet_decrypts_ciphertext_encrypted_with_an_older_key() -> None:
    old_service = _service(KEY_A)
    tenant_id = uuid.uuid4()
    ciphertext = old_service.encrypt_credentials(
        tenant_id, Provider.META, b"old-key-secret"
    )

    rotated_service = _service(KEY_B, KEY_A)
    plaintext = rotated_service.decrypt_credentials(
        tenant_id, Provider.META, ciphertext
    )

    assert plaintext == b"old-key-secret"


# --- Constructor / key validation --------------------------------------------


def test_missing_key_is_rejected() -> None:
    with pytest.raises(CredentialEncryptionUnavailableError):
        CredentialEncryptionService(None)


def test_blank_key_string_is_rejected() -> None:
    with pytest.raises(CredentialEncryptionUnavailableError):
        CredentialEncryptionService("   ")


def test_blank_entry_in_key_list_is_rejected() -> None:
    with pytest.raises(CredentialEncryptionUnavailableError):
        CredentialEncryptionService(f"{KEY_A},  ,{KEY_B}")


def test_malformed_key_is_rejected() -> None:
    with pytest.raises(CredentialEncryptionUnavailableError):
        CredentialEncryptionService("not-a-valid-fernet-key")


def test_keys_are_trimmed_of_surrounding_whitespace() -> None:
    service = _service(f"  {KEY_A}  ")
    tenant_id = uuid.uuid4()

    ciphertext = service.encrypt_credentials(tenant_id, Provider.META, b"trimmed")
    plaintext = service.decrypt_credentials(tenant_id, Provider.META, ciphertext)

    assert plaintext == b"trimmed"


# --- Decryption failure modes --------------------------------------------------


def test_decrypting_with_a_service_missing_the_encrypting_key_fails() -> None:
    encrypting_service = _service(KEY_A)
    other_service = _service(KEY_B)
    tenant_id = uuid.uuid4()
    ciphertext = encrypting_service.encrypt_credentials(
        tenant_id, Provider.META, b"secret"
    )

    with pytest.raises(CredentialDecryptionError):
        other_service.decrypt_credentials(tenant_id, Provider.META, ciphertext)


def test_tampered_ciphertext_is_rejected() -> None:
    service = _service(KEY_A)
    tenant_id = uuid.uuid4()
    ciphertext = service.encrypt_credentials(tenant_id, Provider.META, b"secret")
    tampered = bytearray(ciphertext)
    tampered[-1] ^= 0xFF

    with pytest.raises(CredentialDecryptionError):
        service.decrypt_credentials(tenant_id, Provider.META, bytes(tampered))


def test_tenant_mismatch_is_rejected() -> None:
    service = _service(KEY_A)
    ciphertext = service.encrypt_credentials(
        uuid.uuid4(), Provider.META, b"tenant-a-secret"
    )

    with pytest.raises(CredentialDecryptionError):
        service.decrypt_credentials(uuid.uuid4(), Provider.META, ciphertext)


def test_provider_mismatch_is_rejected() -> None:
    service = _service(KEY_A)
    tenant_id = uuid.uuid4()
    ciphertext = service.encrypt_credentials(tenant_id, Provider.META, b"meta-secret")

    with pytest.raises(CredentialDecryptionError):
        service.decrypt_credentials(tenant_id, Provider.GOOGLE, ciphertext)


def _encrypt_raw_envelope(key: str, envelope_bytes: bytes) -> bytes:
    """Build a validly-Fernet-encrypted token around arbitrary bytes.

    Bypasses CredentialEncryptionService.encrypt_credentials so tests can
    construct a structurally valid (HMAC-authenticated) token whose
    *envelope contents* are corrupt -- simulating drift/corruption that
    happened before encryption rather than ciphertext tampering.
    """
    return MultiFernet([Fernet(key.encode("ascii"))]).encrypt(envelope_bytes)


def test_unsupported_envelope_version_is_rejected() -> None:
    service = _service(KEY_A)
    tenant_id = uuid.uuid4()
    envelope = {
        "envelope_version": 999,
        "tenant_id": str(tenant_id),
        "provider": Provider.META.value,
        "credential_payload": base64.b64encode(b"secret").decode("ascii"),
    }
    ciphertext = _encrypt_raw_envelope(KEY_A, json.dumps(envelope).encode("utf-8"))

    with pytest.raises(CredentialDecryptionError):
        service.decrypt_credentials(tenant_id, Provider.META, ciphertext)


def test_malformed_json_envelope_is_rejected() -> None:
    service = _service(KEY_A)
    tenant_id = uuid.uuid4()
    ciphertext = _encrypt_raw_envelope(KEY_A, b"not-json-at-all")

    with pytest.raises(CredentialDecryptionError):
        service.decrypt_credentials(tenant_id, Provider.META, ciphertext)


def test_malformed_base64_credential_payload_is_rejected() -> None:
    service = _service(KEY_A)
    tenant_id = uuid.uuid4()
    envelope = {
        "envelope_version": 1,
        "tenant_id": str(tenant_id),
        "provider": Provider.META.value,
        "credential_payload": "not-valid-base64!!!",
    }
    ciphertext = _encrypt_raw_envelope(KEY_A, json.dumps(envelope).encode("utf-8"))

    with pytest.raises(CredentialDecryptionError):
        service.decrypt_credentials(tenant_id, Provider.META, ciphertext)


# --- No secret leakage in exception messages ------------------------------------


def test_encryption_unavailable_message_never_contains_key_material() -> None:
    with pytest.raises(CredentialEncryptionUnavailableError) as exc_info:
        CredentialEncryptionService("not-a-valid-fernet-key")

    assert "not-a-valid-fernet-key" not in str(exc_info.value)


def test_decryption_error_message_never_contains_plaintext_ciphertext_or_keys() -> (
    None
):
    service = _service(KEY_A)
    tenant_id = uuid.uuid4()
    ciphertext = service.encrypt_credentials(
        tenant_id, Provider.META, b"top-secret-plaintext"
    )
    tampered = bytearray(ciphertext)
    tampered[-1] ^= 0xFF

    with pytest.raises(CredentialDecryptionError) as exc_info:
        service.decrypt_credentials(tenant_id, Provider.META, bytes(tampered))

    message = str(exc_info.value)
    assert "top-secret-plaintext" not in message
    assert KEY_A not in message
    assert ciphertext.decode("ascii", errors="ignore") not in message


# --- Provider enum refactor regression ------------------------------------------


def test_provider_enum_values_match_existing_database_labels() -> None:
    """Provider's string values are exactly the pre-refactor
    CampaignDeploymentProvider values -- the refactor must not change any
    stored/queryable data.
    """
    assert Provider.META.value == "meta"
    assert Provider.GOOGLE.value == "google"


def test_campaign_deployment_provider_column_uses_canonical_provider_enum() -> None:
    """Regression: CampaignDeployment.provider maps through the single
    canonical Provider enum (app.core.providers) under the original
    PostgreSQL enum type name -- not a second, duplicate enum class.
    """
    from app.models.campaign_deployment import CampaignDeployment

    column = CampaignDeployment.__table__.columns["provider"]

    assert column.type.enum_class is Provider
    assert column.type.name == "campaign_deployment_provider"
