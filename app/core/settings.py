"""Application settings and environment configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "VeriSure Ad Automation"
    app_env: Literal["development", "testing", "production"] = "development"
    debug: bool = True
    api_version: str = "v1"
    database_url: str | None = None
    # TEST_DATABASE_URL is read only by the pytest suite (app/tests/database.py)
    # to build a separate engine/session, dedicated to a test database. The
    # application itself never reads this field.
    test_database_url: str | None = None
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_issuer: str = "verisure-api"
    jwt_audience: str = "verisure-clients"
    # Comma-separated Fernet keys for CredentialEncryptionService (see
    # app/core/security/credential_encryption.py). The first key encrypts;
    # every listed key can decrypt, so rotation is: prepend a new key, keep
    # old keys until nothing on disk still needs them, then drop the old
    # ones. None/blank means encryption is unavailable -- callers that need
    # it fail closed rather than falling back to plaintext. Process startup
    # requires a valid key via validate_startup_config().
    encryption_key: str | None = None
    # Seconds the async publish worker sleeps when the queue is empty or
    # after an unexpected exception (see app/orchestration/publish_worker.py).
    # Must be >= 1: 0 busy-loops, and a negative value makes time.sleep raise
    # ValueError which run_forever would catch and immediately retry.
    publish_job_poll_interval_seconds: int = Field(default=5, ge=1)
    # RUNNING jobs whose started_at is older than this many seconds may be
    # reclaimed by claim_next when no QUEUED job is available (worker crash
    # recovery). Must be >= 1.
    publish_job_stale_after_seconds: int = Field(default=900, ge=1)
    # Observability Foundation Phase 1: stdlib logging only.
    log_level: str = "INFO"
    # Explicit format only; startup validation rejects any other value.
    log_format: Literal["json", "text"] = "text"
    log_service_name: str = "verisure"
    # Comma-separated CORS origins. "*" alone is allowed only outside
    # production, and never together with cors_allow_credentials=True.
    # Production requires an explicit non-empty allowlist (no wildcard).
    cors_allow_origins: str = "*"
    cors_allow_credentials: bool = False
    # None => enabled in development/testing, disabled in production.
    # True is rejected in production (fail-fast; no silent override).
    docs_enabled: bool | None = None

    def cors_origin_list(self) -> list[str]:
        """Return stripped CORS origins from the comma-separated setting."""
        return [
            part.strip()
            for part in self.cors_allow_origins.split(",")
            if part.strip()
        ]

    def resolve_docs_enabled(self) -> bool:
        """Resolve whether OpenAPI docs endpoints should be mounted.

        Must be called only after validate_startup_config() has accepted
        the settings (production + docs_enabled=True is rejected there).
        """
        if self.app_env == "production":
            return False
        if self.docs_enabled is None:
            return True
        return self.docs_enabled


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
