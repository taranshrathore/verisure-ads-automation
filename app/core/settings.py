"""Application settings and environment configuration."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "VeriSure Ad Automation"
    app_env: str = "development"
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
    # it fail closed rather than falling back to plaintext.
    encryption_key: str | None = None
    # Seconds the async publish worker sleeps when the queue is empty or
    # after an unexpected exception (see app/orchestration/publish_worker.py).
    # Must be >= 1: 0 busy-loops, and a negative value makes time.sleep raise
    # ValueError which run_forever would catch and immediately retry.
    publish_job_poll_interval_seconds: int = Field(default=5, ge=1)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
