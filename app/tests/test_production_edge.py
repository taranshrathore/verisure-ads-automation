"""Production HTTP edge hardening tests (CORS + OpenAPI docs)."""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.core.startup import ConfigurationError, validate_startup_config

_VALID_FERNET = Fernet.generate_key().decode("ascii")
_VALID_JWT = "unit-test-jwt-secret-key-32chars!!"
_PROD_JWT = "production-grade-jwt-secret-key-ok"


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
        "cors_allow_origins": "*",
        "cors_allow_credentials": False,
        "docs_enabled": None,
    }
    values.update(overrides)
    return Settings(**values)


def _build_app(monkeypatch: pytest.MonkeyPatch, cfg: Settings):
    import app.main as main_mod

    validate_startup_config(cfg)
    monkeypatch.setattr(main_mod, "validate_startup_config", lambda: cfg)
    monkeypatch.setattr(main_mod, "configure_logging", lambda: None)
    return main_mod.create_application()


def test_development_docs_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _valid_settings(app_env="development", docs_enabled=None)
    application = _build_app(monkeypatch, cfg)

    assert application.docs_url == "/docs"
    assert application.redoc_url == "/redoc"
    assert application.openapi_url == "/openapi.json"

    with TestClient(application) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_production_docs_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _valid_settings(
        app_env="production",
        jwt_secret_key=_PROD_JWT,
        cors_allow_origins="https://app.example.com",
        cors_allow_credentials=True,
        docs_enabled=False,
    )
    application = _build_app(monkeypatch, cfg)

    assert application.docs_url is None
    assert application.redoc_url is None

    with TestClient(application) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404


def test_production_openapi_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _valid_settings(
        app_env="production",
        jwt_secret_key=_PROD_JWT,
        cors_allow_origins="https://app.example.com",
        docs_enabled=None,
    )
    application = _build_app(monkeypatch, cfg)

    assert application.openapi_url is None
    with TestClient(application) as client:
        assert client.get("/openapi.json").status_code == 404


def test_valid_production_cors_allowlist() -> None:
    cfg = _valid_settings(
        app_env="production",
        jwt_secret_key=_PROD_JWT,
        cors_allow_origins="https://app.example.com, https://admin.example.com",
        cors_allow_credentials=True,
        docs_enabled=False,
    )
    assert validate_startup_config(cfg) is cfg
    assert cfg.cors_origin_list() == [
        "https://app.example.com",
        "https://admin.example.com",
    ]


def test_wildcard_plus_credentials_rejected() -> None:
    with pytest.raises(ConfigurationError, match="CORS_ALLOW_CREDENTIALS"):
        validate_startup_config(
            _valid_settings(
                cors_allow_origins="*",
                cors_allow_credentials=True,
            )
        )


def test_startup_validation_rejects_production_wildcard_cors() -> None:
    with pytest.raises(ConfigurationError, match="allowlist"):
        validate_startup_config(
            _valid_settings(
                app_env="production",
                jwt_secret_key=_PROD_JWT,
                cors_allow_origins="*",
                cors_allow_credentials=False,
            )
        )


def test_startup_validation_rejects_production_empty_cors() -> None:
    with pytest.raises(ConfigurationError, match="CORS_ALLOW_ORIGINS"):
        validate_startup_config(
            _valid_settings(
                app_env="production",
                jwt_secret_key=_PROD_JWT,
                cors_allow_origins="",
            )
        )


def test_startup_validation_rejects_production_docs_enabled() -> None:
    with pytest.raises(ConfigurationError, match="DOCS_ENABLED"):
        validate_startup_config(
            _valid_settings(
                app_env="production",
                jwt_secret_key=_PROD_JWT,
                cors_allow_origins="https://app.example.com",
                docs_enabled=True,
            )
        )


def test_api_boots_successfully_with_valid_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _valid_settings(
        app_env="testing",
        cors_allow_origins="http://localhost:3000",
        cors_allow_credentials=True,
        docs_enabled=True,
    )
    application = _build_app(monkeypatch, cfg)

    with TestClient(application) as client:
        response = client.get("/api/v1/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"


def test_production_app_boots_with_valid_edge_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _valid_settings(
        app_env="production",
        jwt_secret_key=_PROD_JWT,
        cors_allow_origins="https://app.example.com",
        cors_allow_credentials=True,
        docs_enabled=False,
        log_format="json",
    )
    application = _build_app(monkeypatch, cfg)

    with TestClient(application) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        assert client.get("/docs").status_code == 404
