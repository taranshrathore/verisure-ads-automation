"""Service-level tests for ProviderConnectionService.

Constructs ProviderConnectionRepository/CredentialEncryptionService/
ProviderConnectionService directly against db_session (the same
savepoint-isolated session used elsewhere), independent of any HTTP
layer -- no API exists for provider connections yet. All "credentials"
here are opaque placeholder bytes, never real secrets.
"""

import uuid
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.core.exceptions import (
    CredentialDecryptionError,
    InvalidProviderConnectionStateError,
    ProviderConnectionAlreadyExistsError,
    ProviderConnectionNotFoundError,
)
from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService
from app.models.provider_connection import ProviderConnection, ProviderConnectionStatus
from app.models.tenant import Tenant
from app.repositories.provider_connection_repository import (
    ProviderConnectionRepository,
)
from app.services.provider_connection_service import ProviderConnectionService

_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


class _FailingEncryptionService:
    """Duck-typed stand-in for CredentialEncryptionService whose
    encrypt_credentials always raises, to force connect()'s rollback path
    without touching the real cryptography.fernet dependency.
    """

    def encrypt_credentials(
        self, tenant_id: uuid.UUID, provider: Provider, credential_payload: bytes
    ) -> bytes:
        raise RuntimeError("simulated encryption failure")

    def decrypt_credentials(
        self, tenant_id: uuid.UUID, provider: Provider, ciphertext: bytes
    ) -> bytes:
        raise AssertionError("not exercised by this test")


@pytest.fixture
def connection_repository(db_session: Session) -> ProviderConnectionRepository:
    return ProviderConnectionRepository(db_session)


@pytest.fixture
def encryption_service() -> CredentialEncryptionService:
    return CredentialEncryptionService(_TEST_ENCRYPTION_KEY)


@pytest.fixture
def service(
    connection_repository: ProviderConnectionRepository,
    encryption_service: CredentialEncryptionService,
    db_session: Session,
) -> ProviderConnectionService:
    return ProviderConnectionService(connection_repository, encryption_service, db_session)


def _make_tenant(db_session: Session, *, suffix: str) -> Tenant:
    tenant = Tenant(
        name=f"Provider Connection Svc Tenant {suffix}",
        slug=f"provider-connection-svc-{suffix}",
    )
    db_session.add(tenant)
    db_session.flush()
    return tenant


# --- connect -------------------------------------------------------------------


def test_connect_happy_path(service: ProviderConnectionService, db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="a")

    connection = service.connect(
        tenant_id=tenant.id,
        provider=Provider.META,
        credential_payload=b"raw-secret-token",
        external_account_id="acct-123",
        display_name="Main Ad Account",
    )

    assert connection.tenant_id == tenant.id
    assert connection.provider == Provider.META
    assert connection.status == ProviderConnectionStatus.CONNECTED
    assert connection.external_account_id == "acct-123"
    assert connection.display_name == "Main Ad Account"
    assert connection.disconnected_at is None


def test_duplicate_connected_connect_raises_already_exists(
    service: ProviderConnectionService, db_session: Session
) -> None:
    tenant = _make_tenant(db_session, suffix="b")
    service.connect(
        tenant_id=tenant.id, provider=Provider.META, credential_payload=b"first"
    )

    with pytest.raises(ProviderConnectionAlreadyExistsError):
        service.connect(
            tenant_id=tenant.id, provider=Provider.META, credential_payload=b"second"
        )


def test_disconnected_row_cannot_reconnect(
    service: ProviderConnectionService, db_session: Session
) -> None:
    tenant = _make_tenant(db_session, suffix="c")
    connection = service.connect(
        tenant_id=tenant.id, provider=Provider.META, credential_payload=b"first"
    )
    service.disconnect(tenant_id=tenant.id, connection_id=connection.id)

    with pytest.raises(InvalidProviderConnectionStateError):
        service.connect(
            tenant_id=tenant.id, provider=Provider.META, credential_payload=b"retry"
        )


def test_connect_encrypts_before_persistence(
    service: ProviderConnectionService,
    encryption_service: CredentialEncryptionService,
    db_session: Session,
) -> None:
    tenant = _make_tenant(db_session, suffix="d")
    payload = b"plaintext-should-never-be-stored"

    connection = service.connect(
        tenant_id=tenant.id, provider=Provider.META, credential_payload=payload
    )

    assert connection.encrypted_credentials is not None
    assert connection.encrypted_credentials != payload
    assert payload not in connection.encrypted_credentials
    decrypted = encryption_service.decrypt_credentials(
        tenant.id, Provider.META, connection.encrypted_credentials
    )
    assert decrypted == payload


# --- disconnect --------------------------------------------------------------


def test_disconnect_happy_path(
    service: ProviderConnectionService, db_session: Session
) -> None:
    tenant = _make_tenant(db_session, suffix="e")
    connection = service.connect(
        tenant_id=tenant.id, provider=Provider.META, credential_payload=b"secret"
    )

    disconnected = service.disconnect(tenant_id=tenant.id, connection_id=connection.id)

    assert disconnected.status == ProviderConnectionStatus.DISCONNECTED
    assert disconnected.encrypted_credentials is None
    assert disconnected.disconnected_at is not None


def test_disconnect_bumps_updated_at(
    service: ProviderConnectionService, db_session: Session
) -> None:
    """Regression: Core UPDATE disconnect previously left updated_at
    stale because TimestampMixin.onupdate is ORM-only.
    """
    tenant = _make_tenant(db_session, suffix="e2")
    connection = service.connect(
        tenant_id=tenant.id, provider=Provider.META, credential_payload=b"secret"
    )
    updated_before = connection.updated_at

    disconnected = service.disconnect(tenant_id=tenant.id, connection_id=connection.id)

    assert disconnected.updated_at == disconnected.disconnected_at
    assert disconnected.updated_at >= updated_before


def test_concurrent_connect_unique_violation_maps_to_already_exists(
    service: ProviderConnectionService,
    connection_repository: ProviderConnectionRepository,
    db_session: Session,
) -> None:
    """Regression: a TOCTOU race where get_by_provider returns None but
    another transaction has already inserted the (tenant, provider) row
    must surface as ProviderConnectionAlreadyExistsError (409), not a raw
    IntegrityError (500).
    """
    tenant = _make_tenant(db_session, suffix="b2")
    db_session.commit()
    service.connect(
        tenant_id=tenant.id, provider=Provider.META, credential_payload=b"first"
    )

    original = connection_repository.get_by_provider
    connection_repository.get_by_provider = lambda *a, **k: None  # type: ignore[method-assign]
    try:
        with pytest.raises(ProviderConnectionAlreadyExistsError):
            service.connect(
                tenant_id=tenant.id,
                provider=Provider.META,
                credential_payload=b"racing-second",
            )
    finally:
        connection_repository.get_by_provider = original

    # Session must remain usable after the translated IntegrityError path.
    other = service.connect(
        tenant_id=tenant.id, provider=Provider.GOOGLE, credential_payload=b"ok"
    )
    assert other.provider == Provider.GOOGLE


def test_double_disconnect_raises_conflict(
    service: ProviderConnectionService, db_session: Session
) -> None:
    tenant = _make_tenant(db_session, suffix="f")
    connection = service.connect(
        tenant_id=tenant.id, provider=Provider.META, credential_payload=b"secret"
    )
    service.disconnect(tenant_id=tenant.id, connection_id=connection.id)

    with pytest.raises(InvalidProviderConnectionStateError):
        service.disconnect(tenant_id=tenant.id, connection_id=connection.id)


def test_cross_tenant_disconnect_raises_not_found(
    service: ProviderConnectionService, db_session: Session
) -> None:
    tenant_a = _make_tenant(db_session, suffix="g1")
    tenant_b = _make_tenant(db_session, suffix="g2")
    connection = service.connect(
        tenant_id=tenant_a.id, provider=Provider.META, credential_payload=b"secret"
    )

    with pytest.raises(ProviderConnectionNotFoundError):
        service.disconnect(tenant_id=tenant_b.id, connection_id=connection.id)


# --- reads ---------------------------------------------------------------------


def test_get_connection_tenant_isolation(
    service: ProviderConnectionService, db_session: Session
) -> None:
    tenant_a = _make_tenant(db_session, suffix="h1")
    tenant_b = _make_tenant(db_session, suffix="h2")
    connection = service.connect(
        tenant_id=tenant_a.id, provider=Provider.META, credential_payload=b"secret"
    )

    fetched = service.get_connection(tenant_id=tenant_a.id, connection_id=connection.id)
    assert fetched.id == connection.id

    with pytest.raises(ProviderConnectionNotFoundError):
        service.get_connection(tenant_id=tenant_b.id, connection_id=connection.id)


def test_list_connections_tenant_isolation(
    service: ProviderConnectionService, db_session: Session
) -> None:
    tenant_a = _make_tenant(db_session, suffix="i1")
    tenant_b = _make_tenant(db_session, suffix="i2")
    own = service.connect(
        tenant_id=tenant_a.id, provider=Provider.META, credential_payload=b"secret-a"
    )
    service.connect(
        tenant_id=tenant_b.id, provider=Provider.META, credential_payload=b"secret-b"
    )

    result = service.list_connections(tenant_id=tenant_a.id)

    assert [row.id for row in result] == [own.id]


# --- get_decrypted_credentials -------------------------------------------------


def test_get_decrypted_credentials_happy_path(
    service: ProviderConnectionService, db_session: Session
) -> None:
    tenant = _make_tenant(db_session, suffix="j")
    payload = b"the-real-secret-bytes"
    service.connect(
        tenant_id=tenant.id, provider=Provider.META, credential_payload=payload
    )

    decrypted = service.get_decrypted_credentials(
        tenant_id=tenant.id, provider=Provider.META
    )

    assert decrypted == payload


def test_get_decrypted_credentials_missing_provider_raises_not_found(
    service: ProviderConnectionService, db_session: Session
) -> None:
    tenant = _make_tenant(db_session, suffix="k")

    with pytest.raises(ProviderConnectionNotFoundError):
        service.get_decrypted_credentials(tenant_id=tenant.id, provider=Provider.META)


def test_get_decrypted_credentials_disconnected_provider_raises_not_found(
    service: ProviderConnectionService, db_session: Session
) -> None:
    tenant = _make_tenant(db_session, suffix="l")
    connection = service.connect(
        tenant_id=tenant.id, provider=Provider.META, credential_payload=b"secret"
    )
    service.disconnect(tenant_id=tenant.id, connection_id=connection.id)

    with pytest.raises(ProviderConnectionNotFoundError):
        service.get_decrypted_credentials(tenant_id=tenant.id, provider=Provider.META)


def test_get_decrypted_credentials_decrypt_failure_propagates(
    connection_repository: ProviderConnectionRepository,
    encryption_service: CredentialEncryptionService,
    db_session: Session,
) -> None:
    """Ciphertext encrypted under a key this service does not have
    configured must surface as CredentialDecryptionError, not be
    swallowed or misreported as ProviderConnectionNotFoundError.
    """
    tenant = _make_tenant(db_session, suffix="m")
    other_encryption_service = CredentialEncryptionService(
        Fernet.generate_key().decode("ascii")
    )
    wrong_key_ciphertext = other_encryption_service.encrypt_credentials(
        tenant.id, Provider.META, b"secret"
    )
    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.META,
        status=ProviderConnectionStatus.CONNECTED,
        encrypted_credentials=wrong_key_ciphertext,
    )
    db_session.add(connection)
    db_session.flush()
    service = ProviderConnectionService(
        connection_repository, encryption_service, db_session
    )

    with pytest.raises(CredentialDecryptionError):
        service.get_decrypted_credentials(tenant_id=tenant.id, provider=Provider.META)


# --- transaction / rollback behavior --------------------------------------------


def test_connect_rolls_back_on_encryption_failure(
    connection_repository: ProviderConnectionRepository,
    service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant = _make_tenant(db_session, suffix="n")
    # Checkpoint setup as its own SAVEPOINT (see conftest.py's db_session
    # fixture) so the rollback below only discards the failed connect
    # attempt, not this tenant row too.
    db_session.commit()
    failing_service = ProviderConnectionService(
        connection_repository, _FailingEncryptionService(), db_session
    )

    with pytest.raises(RuntimeError, match="simulated encryption failure"):
        failing_service.connect(
            tenant_id=tenant.id, provider=Provider.META, credential_payload=b"secret"
        )

    assert connection_repository.get_by_provider(tenant.id, Provider.META) is None
    # The session must still be usable for a completely unrelated call,
    # made through the real (working) service.
    connection = service.connect(
        tenant_id=tenant.id, provider=Provider.GOOGLE, credential_payload=b"secret"
    )
    assert connection.status == ProviderConnectionStatus.CONNECTED


def test_connect_rolls_back_on_repository_failure(
    connection_repository: ProviderConnectionRepository,
    service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant = _make_tenant(db_session, suffix="o")
    db_session.commit()  # checkpoint, see test above

    original_create = connection_repository.create

    def _raise_simulated_db_failure(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated database failure")

    connection_repository.create = _raise_simulated_db_failure  # type: ignore[method-assign]

    try:
        with pytest.raises(RuntimeError, match="simulated database failure"):
            service.connect(
                tenant_id=tenant.id,
                provider=Provider.META,
                credential_payload=b"secret",
            )
    finally:
        connection_repository.create = original_create

    assert connection_repository.get_by_provider(tenant.id, Provider.META) is None
    # The session must still be usable for a completely unrelated call.
    connection = service.connect(
        tenant_id=tenant.id, provider=Provider.GOOGLE, credential_payload=b"secret"
    )
    assert connection.status == ProviderConnectionStatus.CONNECTED


def test_session_usable_after_a_domain_exception_rollback(
    service: ProviderConnectionService, db_session: Session
) -> None:
    """A plain domain-exception rollback (no persistence/encryption
    failure involved) must also leave the session usable for a
    completely unrelated subsequent call.
    """
    tenant = _make_tenant(db_session, suffix="p")
    # Checkpoint setup as its own SAVEPOINT (see conftest.py's db_session
    # fixture) so the service's internal rollback below only discards the
    # failed disconnect attempt, not this tenant row too.
    db_session.commit()

    with pytest.raises(ProviderConnectionNotFoundError):
        service.disconnect(tenant_id=tenant.id, connection_id=uuid.uuid4())

    connection = service.connect(
        tenant_id=tenant.id, provider=Provider.META, credential_payload=b"secret"
    )
    assert connection.status == ProviderConnectionStatus.CONNECTED


def test_repository_never_commits(
    connection_repository: ProviderConnectionRepository, db_session: Session
) -> None:
    """The repository instance the service depends on never commits --
    calling its methods directly and then rolling back discards
    everything, proving no commit happened inside the repository itself
    (the service is solely responsible for committing).
    """
    tenant = _make_tenant(db_session, suffix="q")
    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.META,
        status=ProviderConnectionStatus.CONNECTED,
        encrypted_credentials=b"never-committed-ciphertext",
    )

    connection_repository.create(connection)
    db_session.flush()
    connection_id = connection.id
    connection_repository.disconnect(tenant.id, connection_id, datetime.now(timezone.utc))
    db_session.flush()

    db_session.rollback()

    assert db_session.get(ProviderConnection, connection_id) is None
