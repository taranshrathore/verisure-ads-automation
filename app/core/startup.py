"""Fail-fast startup configuration validation.

Single shared path used by the API and the publish worker. Never
auto-generates secrets, never silently falls back, and never continues
on invalid configuration.
"""

from __future__ import annotations

from sqlalchemy.engine.url import make_url

from app.core.exceptions import CredentialEncryptionUnavailableError
from app.core.security.credential_encryption import CredentialEncryptionService
from app.core.settings import Settings, settings

_ALLOWED_APP_ENVS = frozenset({"development", "testing", "production"})
_ALLOWED_LOG_LEVELS = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)
_ALLOWED_LOG_FORMATS = frozenset({"text", "json"})
_INSECURE_JWT_SECRETS = frozenset(
    {
        "secret",
        "changeme",
        "password",
        "dev",
        "dev-only-change-me",
    }
)
_MIN_JWT_SECRET_LENGTH = 32


class ConfigurationError(RuntimeError):
    """Raised when critical process configuration is invalid."""


def validate_startup_config(cfg: Settings | None = None) -> Settings:
    """Validate critical settings once at process startup.

    Returns the validated Settings instance. Raises ConfigurationError
    on the first fatal problem. Does not mutate settings or invent
    replacement secrets/URLs/keys.
    """
    resolved = cfg if cfg is not None else settings

    _validate_app_env(resolved)
    _validate_database_url(resolved)
    _validate_jwt_secret(resolved)
    _validate_encryption_key(resolved)
    _validate_log_level(resolved)
    _validate_log_format(resolved)
    _validate_publish_intervals(resolved)
    _validate_cors(resolved)
    _validate_docs(resolved)
    return resolved


def _validate_app_env(cfg: Settings) -> None:
    if cfg.app_env not in _ALLOWED_APP_ENVS:
        raise ConfigurationError(
            "APP_ENV must be one of: development, testing, production."
        )


def _validate_database_url(cfg: Settings) -> None:
    raw = cfg.database_url
    if raw is None or not str(raw).strip():
        raise ConfigurationError("DATABASE_URL is required.")
    try:
        make_url(str(raw).strip())
    except Exception as exc:
        raise ConfigurationError(
            "DATABASE_URL is not a valid SQLAlchemy URL."
        ) from exc


def _validate_jwt_secret(cfg: Settings) -> None:
    secret = cfg.jwt_secret_key
    if secret is None or not str(secret).strip():
        raise ConfigurationError("JWT_SECRET_KEY is required.")
    normalized = secret.strip().lower()
    if cfg.app_env == "production" and normalized in _INSECURE_JWT_SECRETS:
        raise ConfigurationError(
            "JWT_SECRET_KEY uses an insecure default value which is not "
            "allowed in production."
        )
    if len(secret) < _MIN_JWT_SECRET_LENGTH:
        raise ConfigurationError(
            f"JWT_SECRET_KEY must be at least {_MIN_JWT_SECRET_LENGTH} characters."
        )


def _validate_encryption_key(cfg: Settings) -> None:
    try:
        CredentialEncryptionService(cfg.encryption_key)
    except CredentialEncryptionUnavailableError as exc:
        raise ConfigurationError(str(exc)) from exc


def _validate_log_level(cfg: Settings) -> None:
    level = (cfg.log_level or "").strip().upper()
    if level not in _ALLOWED_LOG_LEVELS:
        raise ConfigurationError(
            "LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL."
        )


def _validate_log_format(cfg: Settings) -> None:
    fmt = cfg.log_format
    if fmt not in _ALLOWED_LOG_FORMATS:
        raise ConfigurationError("LOG_FORMAT must be one of: text, json.")


def _validate_publish_intervals(cfg: Settings) -> None:
    if cfg.publish_job_poll_interval_seconds < 1:
        raise ConfigurationError(
            "PUBLISH_JOB_POLL_INTERVAL_SECONDS must be >= 1."
        )
    if cfg.publish_job_stale_after_seconds < 1:
        raise ConfigurationError(
            "PUBLISH_JOB_STALE_AFTER_SECONDS must be >= 1."
        )


def _validate_cors(cfg: Settings) -> None:
    origins = cfg.cors_origin_list()
    if "*" in origins:
        if len(origins) != 1:
            raise ConfigurationError(
                "CORS_ALLOW_ORIGINS wildcard '*' must be the sole origin."
            )
        if cfg.cors_allow_credentials:
            raise ConfigurationError(
                "CORS_ALLOW_ORIGINS=['*'] cannot be combined with "
                "CORS_ALLOW_CREDENTIALS=true."
            )
        if cfg.app_env == "production":
            raise ConfigurationError(
                "Production CORS_ALLOW_ORIGINS must be an explicit allowlist; "
                "wildcard '*' is not allowed."
            )
        return

    if cfg.app_env == "production" and not origins:
        raise ConfigurationError(
            "Production CORS_ALLOW_ORIGINS must be a non-empty explicit "
            "allowlist."
        )


def _validate_docs(cfg: Settings) -> None:
    if cfg.app_env == "production" and cfg.docs_enabled is True:
        raise ConfigurationError(
            "DOCS_ENABLED cannot be true in production."
        )
