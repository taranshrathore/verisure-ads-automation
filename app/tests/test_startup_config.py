"""Startup configuration hardening tests.

Validates the shared validate_startup_config() path used by API and worker.
Does not auto-generate secrets. No sleeps.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.settings import Settings
from app.core.startup import ConfigurationError, validate_startup_config

_VALID_FERNET = Fernet.generate_key().decode("ascii")
_VALID_JWT = "unit-test-jwt-secret-key-32chars!!"


def _valid_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "development",
        "database_url": "postgresql+psycopg://user:pass@localhost:5432/verisure",
        "jwt_secret_key": _VALID_JWT,
        "encryption_key": _VALID_FERNET,
        "log_level": "INFO",
        "log_format": "text",
        "publish_job_poll_interval_seconds": 5,
        "publish_job_stale_after_seconds": 900,
    }
    values.update(overrides)
    return Settings(**values)


def test_valid_config_passes() -> None:
    cfg = _valid_settings()
    assert validate_startup_config(cfg) is cfg


def test_missing_database_url_fails() -> None:
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        validate_startup_config(_valid_settings(database_url=None))


def test_invalid_database_url_fails() -> None:
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        validate_startup_config(
            _valid_settings(database_url="not-a-sqlalchemy-url")
        )


def test_invalid_jwt_length_fails() -> None:
    with pytest.raises(ConfigurationError, match="at least 32"):
        validate_startup_config(
            _valid_settings(jwt_secret_key="short-but-not-listed")
        )


@pytest.mark.parametrize(
    "insecure",
    ["secret", "changeme", "password", "dev", "dev-only-change-me"],
)
def test_insecure_production_jwt_fails(insecure: str) -> None:
    with pytest.raises(ConfigurationError, match="insecure"):
        validate_startup_config(
            _valid_settings(app_env="production", jwt_secret_key=insecure)
        )


def test_insecure_jwt_allowed_outside_production_when_long_enough() -> None:
    cfg = _valid_settings(
        app_env="development",
        jwt_secret_key="dev-only-change-me-not-for-production",
    )
    assert validate_startup_config(cfg) is cfg


def test_invalid_fernet_key_fails() -> None:
    with pytest.raises(ConfigurationError, match="[Ee]ncryption"):
        validate_startup_config(_valid_settings(encryption_key="not-a-fernet-key"))


def test_missing_encryption_key_fails() -> None:
    with pytest.raises(ConfigurationError, match="[Ee]ncryption"):
        validate_startup_config(_valid_settings(encryption_key=None))


def test_invalid_log_level_fails() -> None:
    with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
        validate_startup_config(_valid_settings(log_level="VERBOSE"))


def test_invalid_log_format_fails() -> None:
    with pytest.raises(ValidationError):
        Settings(
            jwt_secret_key=_VALID_JWT,
            log_format="xml",  # type: ignore[arg-type]
        )


def test_invalid_poll_interval_fails() -> None:
    with pytest.raises(ValidationError):
        Settings(
            jwt_secret_key=_VALID_JWT,
            encryption_key=_VALID_FERNET,
            database_url="postgresql+psycopg://u:p@localhost/db",
            publish_job_poll_interval_seconds=0,
        )


def test_invalid_stale_timeout_fails() -> None:
    with pytest.raises(ValidationError):
        Settings(
            jwt_secret_key=_VALID_JWT,
            encryption_key=_VALID_FERNET,
            database_url="postgresql+psycopg://u:p@localhost/db",
            publish_job_stale_after_seconds=0,
        )


def test_startup_rejects_poll_interval_below_one_via_validate() -> None:
    cfg = _valid_settings()
    object.__setattr__(cfg, "publish_job_poll_interval_seconds", 0)
    with pytest.raises(ConfigurationError, match="PUBLISH_JOB_POLL_INTERVAL"):
        validate_startup_config(cfg)


def test_startup_rejects_stale_timeout_below_one_via_validate() -> None:
    cfg = _valid_settings()
    object.__setattr__(cfg, "publish_job_stale_after_seconds", 0)
    with pytest.raises(ConfigurationError, match="PUBLISH_JOB_STALE_AFTER"):
        validate_startup_config(cfg)


def test_invalid_app_env_fails() -> None:
    with pytest.raises(ValidationError):
        Settings(
            jwt_secret_key=_VALID_JWT,
            app_env="staging",  # type: ignore[arg-type]
        )


def test_api_uses_shared_validation() -> None:
    import app.main as main_mod

    assert main_mod.validate_startup_config is validate_startup_config
    source = inspect.getsource(main_mod.create_application)
    assert "validate_startup_config()" in source


def test_worker_uses_shared_validation() -> None:
    from app.orchestration import publish_worker

    assert publish_worker.validate_startup_config is validate_startup_config
    source = inspect.getsource(publish_worker.main)
    assert "validate_startup_config()" in source


def test_worker_main_invokes_shared_validate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.orchestration import publish_worker

    calls: list[str] = []

    def _validate(cfg: Settings | None = None) -> Settings:
        del cfg
        calls.append("validate")
        return _valid_settings()

    monkeypatch.setattr(publish_worker, "validate_startup_config", _validate)
    monkeypatch.setattr(publish_worker, "configure_logging", lambda: None)
    monkeypatch.setattr(publish_worker, "run_forever", lambda: None)

    publish_worker.main()
    assert calls == ["validate"]


def test_create_application_invokes_shared_validate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as main_mod

    calls: list[str] = []
    cfg = _valid_settings()

    def _validate(incoming: Settings | None = None) -> Settings:
        del incoming
        calls.append("validate")
        return cfg

    monkeypatch.setattr(main_mod, "validate_startup_config", _validate)
    monkeypatch.setattr(main_mod, "configure_logging", lambda: None)
    monkeypatch.setattr(
        main_mod, "register_exception_handlers", lambda _app: None
    )

    application = main_mod.create_application()
    assert calls == ["validate"]
    assert application.title == cfg.app_name
