"""Repository-level tests for ProviderConnectionRepository.

Phase 3 scope: repository only -- no service, no API, no encryption
integration. All "credentials" here are opaque placeholder bytes, never
real secrets.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.providers import Provider
from app.models.provider_connection import ProviderConnection, ProviderConnectionStatus
from app.models.tenant import Tenant
from app.repositories.provider_connection_repository import (
    ProviderConnectionRepository,
)


def _make_tenant(db_session: Session, *, suffix: str) -> Tenant:
    tenant = Tenant(
        name=f"Provider Connection Repo Tenant {suffix}",
        slug=f"provider-connection-repo-{suffix}",
    )
    db_session.add(tenant)
    db_session.flush()
    return tenant


def _make_connected(
    db_session: Session,
    tenant: Tenant,
    *,
    provider: Provider = Provider.META,
    ciphertext: bytes = b"opaque-ciphertext",
) -> ProviderConnection:
    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=provider,
        status=ProviderConnectionStatus.CONNECTED,
        encrypted_credentials=ciphertext,
    )
    db_session.add(connection)
    db_session.flush()
    return connection


# --- create ----------------------------------------------------------------


def test_create_stages_a_row(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="a")
    repo = ProviderConnectionRepository(db_session)
    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.META,
        status=ProviderConnectionStatus.CONNECTED,
        encrypted_credentials=b"staged-ciphertext",
    )

    repo.create(connection)
    db_session.flush()

    fetched = db_session.get(ProviderConnection, connection.id)
    assert fetched is not None
    assert fetched.tenant_id == tenant.id
    assert fetched.provider == Provider.META


# --- get_by_id ---------------------------------------------------------------


def test_get_by_id_is_tenant_scoped(db_session: Session) -> None:
    tenant_a = _make_tenant(db_session, suffix="b1")
    tenant_b = _make_tenant(db_session, suffix="b2")
    connection = _make_connected(db_session, tenant_a)
    repo = ProviderConnectionRepository(db_session)

    assert repo.get_by_id(tenant_a.id, connection.id) is connection
    assert repo.get_by_id(tenant_b.id, connection.id) is None


def test_get_by_id_missing_returns_none(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="b3")
    repo = ProviderConnectionRepository(db_session)

    assert repo.get_by_id(tenant.id, uuid.uuid4()) is None


# --- get_by_provider -----------------------------------------------------------


def test_get_by_provider_is_tenant_scoped(db_session: Session) -> None:
    tenant_a = _make_tenant(db_session, suffix="c1")
    tenant_b = _make_tenant(db_session, suffix="c2")
    connection = _make_connected(db_session, tenant_a, provider=Provider.META)
    repo = ProviderConnectionRepository(db_session)

    assert repo.get_by_provider(tenant_a.id, Provider.META) is connection
    assert repo.get_by_provider(tenant_b.id, Provider.META) is None
    assert repo.get_by_provider(tenant_a.id, Provider.GOOGLE) is None


# --- get_connected_by_provider -------------------------------------------------


def test_get_connected_by_provider_returns_connected_row(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="d1")
    connection = _make_connected(db_session, tenant)
    repo = ProviderConnectionRepository(db_session)

    result = repo.get_connected_by_provider(tenant.id, Provider.META)

    assert result is connection


def test_get_connected_by_provider_excludes_disconnected_row(
    db_session: Session,
) -> None:
    tenant = _make_tenant(db_session, suffix="d2")
    connection = _make_connected(db_session, tenant)
    repo = ProviderConnectionRepository(db_session)
    repo.disconnect(tenant.id, connection.id, datetime.now(timezone.utc))
    db_session.flush()

    result = repo.get_connected_by_provider(tenant.id, Provider.META)

    assert result is None


def test_connected_state_with_null_ciphertext_cannot_be_forced_at_database_level(
    db_session: Session,
) -> None:
    """get_connected_by_provider's `encrypted_credentials IS NOT NULL`
    predicate is defense-in-depth: it can never actually exclude a real
    row, because ck_provider_connections_status_credentials_coherent
    makes a connected+NULL-ciphertext row impossible to persist at all --
    even via a raw UPDATE that bypasses the repository/ORM entirely, as
    demonstrated here. There is therefore no way to build a real
    fixture for "excludes a structurally invalid connected/NULL-
    ciphertext row": the invalid state this predicate defends against
    cannot exist in the database in the first place.
    """
    tenant = _make_tenant(db_session, suffix="d3")
    connection = _make_connected(db_session, tenant)

    with pytest.raises(IntegrityError):
        db_session.execute(
            update(ProviderConnection)
            .where(ProviderConnection.id == connection.id)
            .values(encrypted_credentials=None)
        )


# --- list_by_tenant --------------------------------------------------------------


def test_list_by_tenant_excludes_other_tenants(db_session: Session) -> None:
    tenant_a = _make_tenant(db_session, suffix="e1")
    tenant_b = _make_tenant(db_session, suffix="e2")
    own = _make_connected(db_session, tenant_a, provider=Provider.META)
    _make_connected(db_session, tenant_b, provider=Provider.META)
    repo = ProviderConnectionRepository(db_session)

    result = repo.list_by_tenant(tenant_a.id)

    assert [row.id for row in result] == [own.id]


def test_list_by_tenant_deterministic_ordering(db_session: Session) -> None:
    """Ordered by the provider column then id. The provider column is a
    PostgreSQL enum whose ascending order follows its declared label
    order (meta, google -- see the migration), not alphabetical order --
    rows are deliberately created here in the opposite (google, meta)
    order to prove the result reflects that declared order, not
    insertion order.
    """
    tenant = _make_tenant(db_session, suffix="e3")
    google_connection = _make_connected(db_session, tenant, provider=Provider.GOOGLE)
    meta_connection = _make_connected(db_session, tenant, provider=Provider.META)
    repo = ProviderConnectionRepository(db_session)

    result = repo.list_by_tenant(tenant.id)

    assert [row.id for row in result] == [meta_connection.id, google_connection.id]


# --- disconnect --------------------------------------------------------------


def test_disconnect_clears_ciphertext_sets_timestamp_and_changes_status(
    db_session: Session,
) -> None:
    tenant = _make_tenant(db_session, suffix="f1")
    connection = _make_connected(db_session, tenant)
    repo = ProviderConnectionRepository(db_session)
    disconnected_at = datetime.now(timezone.utc)

    affected = repo.disconnect(tenant.id, connection.id, disconnected_at)
    db_session.refresh(connection)

    assert affected == 1
    assert connection.status == ProviderConnectionStatus.DISCONNECTED
    assert connection.encrypted_credentials is None
    assert connection.disconnected_at == disconnected_at
    # Core UPDATE must set updated_at explicitly -- TimestampMixin.onupdate
    # does not fire for Core statements (regression: previously left stale).
    assert connection.updated_at == disconnected_at


def test_second_disconnect_returns_rowcount_zero(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="f2")
    connection = _make_connected(db_session, tenant)
    repo = ProviderConnectionRepository(db_session)

    first = repo.disconnect(tenant.id, connection.id, datetime.now(timezone.utc))
    second = repo.disconnect(tenant.id, connection.id, datetime.now(timezone.utc))

    assert first == 1
    assert second == 0


def test_cross_tenant_disconnect_returns_rowcount_zero(db_session: Session) -> None:
    tenant_a = _make_tenant(db_session, suffix="f3")
    tenant_b = _make_tenant(db_session, suffix="f4")
    connection = _make_connected(db_session, tenant_a)
    repo = ProviderConnectionRepository(db_session)

    affected = repo.disconnect(tenant_b.id, connection.id, datetime.now(timezone.utc))
    db_session.refresh(connection)

    assert affected == 0
    assert connection.status == ProviderConnectionStatus.CONNECTED
    assert connection.encrypted_credentials is not None


def test_disconnect_conditional_update_prevents_a_concurrent_second_disconnect(
    db_session: Session,
) -> None:
    """Two callers both believing the connection is still CONNECTED race
    to disconnect it. Only the first UPDATE's WHERE (status ==
    CONNECTED) matches; the second finds the row already changed and
    affects zero rows -- proving the DB-level conditional guard, not
    just an earlier Python-level read, is what prevents a double
    disconnect (and a second, spurious ciphertext-clearing write).
    """
    tenant = _make_tenant(db_session, suffix="f5")
    connection = _make_connected(db_session, tenant)
    repo = ProviderConnectionRepository(db_session)
    first_attempt_time = datetime.now(timezone.utc)
    second_attempt_time = first_attempt_time + timedelta(seconds=1)

    first_attempt = repo.disconnect(tenant.id, connection.id, first_attempt_time)
    second_attempt = repo.disconnect(tenant.id, connection.id, second_attempt_time)
    db_session.refresh(connection)

    assert first_attempt == 1
    assert second_attempt == 0
    assert connection.disconnected_at == first_attempt_time


# --- transaction ownership --------------------------------------------------


def test_repository_methods_never_commit(db_session: Session) -> None:
    """create() and disconnect() only stage/execute writes -- neither
    calls session.commit(). Rolling back the session afterward (without
    this test ever committing) discards everything created/mutated here;
    if either method had committed internally, the savepoint-based
    db_session fixture (join_transaction_mode="create_savepoint") would
    have started a fresh savepoint at that commit, and this rollback
    would not undo it.
    """
    tenant = _make_tenant(db_session, suffix="g1")
    repo = ProviderConnectionRepository(db_session)
    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.META,
        status=ProviderConnectionStatus.CONNECTED,
        encrypted_credentials=b"never-committed-ciphertext",
    )

    repo.create(connection)
    db_session.flush()
    connection_id = connection.id
    repo.disconnect(tenant.id, connection_id, datetime.now(timezone.utc))
    db_session.flush()

    db_session.rollback()

    assert db_session.get(ProviderConnection, connection_id) is None
