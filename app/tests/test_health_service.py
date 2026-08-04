"""Unit tests for HealthService (no HTTP, no sleeps)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.services.health_service import HealthService


class _ExplodingSession:
    """Session stand-in that fails with a secret-bearing message."""

    def execute(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError(
            f"could not connect using DATABASE_URL={settings.database_url} "
            "password=super-secret-db-password"
        )


def test_live_has_no_side_effects() -> None:
    assert HealthService.live() == {"status": "alive"}
    assert HealthService().live() == {"status": "alive"}


def test_check_database_success(db_session: Session) -> None:
    service = HealthService(session=db_session)
    assert service.check_database() is True
    assert service.database_status() == {"status": "ok"}


def test_check_database_failure_swallows_exception_text() -> None:
    service = HealthService(session=_ExplodingSession())  # type: ignore[arg-type]
    assert service.check_database() is False
    assert service.database_status() == {"status": "unavailable"}


def test_check_database_without_session_is_false() -> None:
    assert HealthService(session=None).check_database() is False


def test_check_mappers_success() -> None:
    assert HealthService.check_mappers() is True


def test_is_ready_success(db_session: Session) -> None:
    service = HealthService(session=db_session)
    assert service.is_ready() is True
    assert service.ready_status() == {"status": "ready"}


def test_is_ready_fails_when_database_fails() -> None:
    service = HealthService(session=_ExplodingSession())  # type: ignore[arg-type]
    assert service.is_ready() is False
    assert service.ready_status() == {"status": "not_ready"}


def test_is_ready_fails_when_mappers_fail(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> None:
        raise RuntimeError("mapper configuration exploded")

    monkeypatch.setattr(
        "app.services.health_service.configure_mappers", _boom
    )
    service = HealthService(session=db_session)
    assert service.check_mappers() is False
    assert service.is_ready() is False
    assert service.ready_status() == {"status": "not_ready"}


def test_worker_available_when_database_url_configured() -> None:
    assert settings.database_url or settings.test_database_url
    # Worker reads DATABASE_URL; tests usually have it set via .env.
    if settings.database_url:
        assert HealthService.worker_status() == {"status": "available"}


def test_worker_unavailable_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "database_url", None)
    assert HealthService.worker_status() == {"status": "unavailable"}


def test_worker_unavailable_with_invalid_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not settings.database_url:
        monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://x/y")
    monkeypatch.setattr(settings, "publish_job_poll_interval_seconds", 0)
    assert HealthService.worker_status() == {"status": "unavailable"}
