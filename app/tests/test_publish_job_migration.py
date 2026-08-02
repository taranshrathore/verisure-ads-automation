"""Migration round-trip tests for the publish_jobs table (b7e4a91c2d08).

See test_campaign_migration.py for why these run Alembic as a subprocess
against TEST_DATABASE_URL rather than using the db_session/client
fixtures: real DDL is incompatible with the SAVEPOINT-based isolation
those fixtures rely on.

Each test restores the schema to `head` in a finally block so no other
test in the suite is left running against a downgraded schema.
"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import inspect

from app.tests.database import get_test_engine, resolve_test_database_url

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VERSIONS_DIR = _REPO_ROOT / "alembic" / "versions"


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return result


def test_migration_downgrade_and_upgrade_round_trip() -> None:
    """Downgrading past b7e4a91c2d08 then upgrading back to it succeeds
    cleanly, and the publish_jobs table's presence tracks each step.
    """
    test_url = resolve_test_database_url()
    engine = get_test_engine()

    try:
        _run_alembic("downgrade", "c49243d65a23", database_url=test_url)
        with engine.connect() as connection:
            assert "publish_jobs" not in inspect(connection).get_table_names()

        _run_alembic("upgrade", "b7e4a91c2d08", database_url=test_url)
        with engine.connect() as connection:
            assert "publish_jobs" in inspect(connection).get_table_names()
    finally:
        _run_alembic("upgrade", "head", database_url=test_url)


def test_orm_metadata_matches_database_after_round_trip() -> None:
    """After a downgrade+upgrade round trip, a scratch `alembic revision
    --autogenerate` detects zero drift between ORM metadata and the live
    schema (an empty upgrade()/downgrade() body).
    """
    test_url = resolve_test_database_url()

    try:
        _run_alembic("downgrade", "-1", database_url=test_url)
        _run_alembic("upgrade", "head", database_url=test_url)

        before = set(_VERSIONS_DIR.glob("*_publish_job_drift_check_scratch.py"))
        _run_alembic(
            "revision",
            "--autogenerate",
            "-m",
            "publish_job_drift_check_scratch",
            database_url=test_url,
        )
        after = set(_VERSIONS_DIR.glob("*_publish_job_drift_check_scratch.py"))
        new_files = after - before
        assert len(new_files) == 1, (
            "Expected exactly one new scratch revision file, found: "
            f"{new_files}"
        )
        scratch_path = new_files.pop()
        try:
            contents = scratch_path.read_text()
            assert contents.count("pass") == 2, (
                "Alembic autogenerate detected schema drift between ORM "
                f"metadata and the database:\n{contents}"
            )
        finally:
            scratch_path.unlink(missing_ok=True)
    finally:
        _run_alembic("upgrade", "head", database_url=test_url)
