"""Database-level constraint tests for the provider_connections table.

These exercise real PostgreSQL CHECK/UNIQUE/FK constraints directly via
the ORM. No repository or service exists yet (Phase 2 scope is model +
migration only), so every row here is constructed and flushed directly.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.providers import Provider
from app.models.provider_connection import ProviderConnection, ProviderConnectionStatus
from app.models.tenant import Tenant


def _make_tenant(db_session: Session, *, suffix: str) -> Tenant:
    tenant = Tenant(
        name=f"Provider Connection Tenant {suffix}",
        slug=f"provider-connection-{suffix}",
    )
    db_session.add(tenant)
    db_session.flush()
    return tenant


def test_connected_row_is_accepted(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="a")

    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.META,
        status=ProviderConnectionStatus.CONNECTED,
        encrypted_credentials=b"opaque-ciphertext",
    )
    db_session.add(connection)
    db_session.flush()

    assert connection.status == ProviderConnectionStatus.CONNECTED
    assert connection.disconnected_at is None


def test_disconnected_row_is_accepted(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="b")

    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.META,
        status=ProviderConnectionStatus.DISCONNECTED,
        encrypted_credentials=None,
        disconnected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    db_session.flush()

    assert connection.status == ProviderConnectionStatus.DISCONNECTED
    assert connection.encrypted_credentials is None


def test_connected_with_null_ciphertext_is_rejected(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="c")

    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.META,
        status=ProviderConnectionStatus.CONNECTED,
        encrypted_credentials=None,
    )
    db_session.add(connection)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_connected_with_disconnected_at_set_is_rejected(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="d")

    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.META,
        status=ProviderConnectionStatus.CONNECTED,
        encrypted_credentials=b"opaque-ciphertext",
        disconnected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_disconnected_with_ciphertext_present_is_rejected(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="e")

    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.META,
        status=ProviderConnectionStatus.DISCONNECTED,
        encrypted_credentials=b"leftover-ciphertext",
        disconnected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_disconnected_with_null_disconnected_at_is_rejected(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="f")

    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.META,
        status=ProviderConnectionStatus.DISCONNECTED,
        encrypted_credentials=None,
        disconnected_at=None,
    )
    db_session.add(connection)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_duplicate_tenant_provider_is_rejected(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="g")
    db_session.add(
        ProviderConnection(
            tenant_id=tenant.id,
            provider=Provider.META,
            status=ProviderConnectionStatus.CONNECTED,
            encrypted_credentials=b"first-connection",
        )
    )
    db_session.flush()

    db_session.add(
        ProviderConnection(
            tenant_id=tenant.id,
            provider=Provider.META,
            status=ProviderConnectionStatus.CONNECTED,
            encrypted_credentials=b"second-connection",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_same_provider_across_different_tenants_is_allowed(db_session: Session) -> None:
    tenant_a = _make_tenant(db_session, suffix="h")
    tenant_b = _make_tenant(db_session, suffix="i")

    db_session.add(
        ProviderConnection(
            tenant_id=tenant_a.id,
            provider=Provider.META,
            status=ProviderConnectionStatus.CONNECTED,
            encrypted_credentials=b"tenant-a-ciphertext",
        )
    )
    db_session.add(
        ProviderConnection(
            tenant_id=tenant_b.id,
            provider=Provider.META,
            status=ProviderConnectionStatus.CONNECTED,
            encrypted_credentials=b"tenant-b-ciphertext",
        )
    )

    db_session.flush()  # does not raise


def test_provider_enum_stored_as_lowercase_database_value(db_session: Session) -> None:
    tenant = _make_tenant(db_session, suffix="j")
    connection = ProviderConnection(
        tenant_id=tenant.id,
        provider=Provider.GOOGLE,
        status=ProviderConnectionStatus.CONNECTED,
        encrypted_credentials=b"google-ciphertext",
    )
    db_session.add(connection)
    db_session.flush()

    raw_provider, raw_status = db_session.execute(
        text(
            "SELECT provider::text, status::text FROM provider_connections "
            "WHERE id = :id"
        ),
        {"id": connection.id},
    ).one()

    assert raw_provider == "google"
    assert raw_status == "connected"
