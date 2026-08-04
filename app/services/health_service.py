"""Operational health checks (Production Health & Readiness Foundation).

Owns connectivity and configuration probes only. No commits, no
repositories, no business logic, and no lifecycle logging. Failure paths
must never surface DATABASE_URL, stack traces, or exception text.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, configure_mappers

from app.core.settings import settings


class HealthService:
    """Lightweight health probes for liveness, readiness, and worker config."""

    def __init__(self, session: Session | None = None) -> None:
        self._session = session

    @staticmethod
    def live() -> dict[str, str]:
        """Process liveness — no I/O."""
        return {"status": "alive"}

    def check_database(self) -> bool:
        """Return True when a lightweight DB round-trip succeeds."""
        if self._session is None:
            return False
        try:
            self._session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    @staticmethod
    def check_mappers() -> bool:
        """Return True when SQLAlchemy mappers configure cleanly."""
        try:
            # Ensure model modules are registered before configuring mappers.
            __import__("app.models")
            configure_mappers()
            return True
        except Exception:
            return False

    def is_ready(self) -> bool:
        """Ready when the database responds and mappers configure."""
        return self.check_database() and self.check_mappers()

    def database_status(self) -> dict[str, str]:
        """Database-only status payload (never includes connection details)."""
        if self.check_database():
            return {"status": "ok"}
        return {"status": "unavailable"}

    def ready_status(self) -> dict[str, str]:
        """Aggregate readiness payload."""
        if self.is_ready():
            return {"status": "ready"}
        return {"status": "not_ready"}

    @staticmethod
    def worker_status() -> dict[str, str]:
        """Report whether worker configuration is valid for local execution.

        No IPC and no distributed coordination — only settings the
        standalone publish worker requires to construct a session and
        poll safely.
        """
        try:
            if not settings.database_url:
                return {"status": "unavailable"}
            if settings.publish_job_poll_interval_seconds < 1:
                return {"status": "unavailable"}
            return {"status": "available"}
        except Exception:
            return {"status": "unavailable"}
