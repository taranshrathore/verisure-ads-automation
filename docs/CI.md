# Backend Continuous Integration

## Purpose

GitHub Actions workflow **Backend CI** (`.github/workflows/backend.yml`) is the
production CI foundation for this repository. It validates every `push` and
`pull_request` against a disposable PostgreSQL service container using
**test-safe credentials only**.

CI covers:

1. Static import hygiene (`pyflakes app`)
2. Application import (`from app.main import app`)
3. SQLAlchemy mapper configuration
4. Alembic migrations to head
5. Full pytest suite against a dedicated test database
6. Alembic autogenerate drift detection

CI does **not** deploy, publish Docker images, use GitHub Environments, or
require production secrets.

Dependabot (`.github/dependabot.yml`) opens weekly PRs for **GitHub Actions**
and **Python (pip)** dependencies only. There is no npm ecosystem in this
backend.

## Local execution (mirror CI)

Prerequisites match the README Testing section: a running Postgres, a
dedicated test database whose name contains `test`, and both `DATABASE_URL`
and `TEST_DATABASE_URL` set (they must differ).

```bash
uv sync --locked
uv run --with pyflakes pyflakes app
uv run python -c "from app.main import app"
uv run python -c "from sqlalchemy.orm import configure_mappers; configure_mappers(); print('mappers_ok')"
uv run alembic upgrade head
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
uv run pytest -q
```

### Local drift check

```bash
uv run alembic revision --autogenerate -m "ci_scratch_drift_check"
# Inspect the generated file under alembic/versions/.
# upgrade() and downgrade() must contain only `pass` (plus an optional docstring).
# Delete the scratch revision afterward — never commit it.
```

PowerShell equivalents for the env override are in the README Testing section.

## PostgreSQL service in CI

The workflow starts `postgres:17` as a GitHub Actions **service container**
with:

- user / password / db: `verisure_ci` / `verisure_ci` / `verisure_ci_db`
- health check: `pg_isready` until healthy (job steps wait on that health)

CI then creates a second database, `verisure_test_db`, so that:

- `DATABASE_URL` → `verisure_ci_db` (Alembic / app-shaped URL)
- `TEST_DATABASE_URL` → `verisure_test_db` (pytest; name contains `test`)

Both URLs receive `alembic upgrade head`. Pytest’s fail-closed guard in
`app/tests/database.py` rejects identical URLs or non-`test` database names.

`JWT_SECRET_KEY` in CI is a fixed non-production placeholder required only so
settings can load. No Meta/Google/OAuth secrets are used.

## Drift failures

The final CI step runs `alembic revision --autogenerate` into a temporary
scratch file, then AST-checks that `upgrade()` and `downgrade()` are
**pass-only**. The scratch file is always deleted afterward.

If this step fails:

1. Models and migrations are out of sync.
2. Generate a real migration locally (`uv run alembic revision --autogenerate -m "..."`).
3. Review the ops, commit the revision, and re-run CI.
4. Never commit a `ci_scratch_drift_check` file.

Empty autogenerate (only `pass`) means **no drift** — CI passes.

## Caching

CI restores a workspace uv cache (`.uv-cache`) and `.venv` keyed on
`uv.lock` + `pyproject.toml`, then runs `uv sync --locked`. This caches both
the uv download cache and installed Python dependencies without publishing
anything.

## Common failure modes

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `TEST_DATABASE_URL is not set` / identical to `DATABASE_URL` | Env misconfigured | Keep both URLs set and distinct; test DB name must contain `test` |
| `DATABASE_URL must be configured before running Alembic` | Missing env in a local shell | Export `DATABASE_URL` before `alembic` |
| `jwt_secret_key` / settings validation error | Missing `JWT_SECRET_KEY` | Set a local-only secret (never commit real production keys) |
| pyflakes fails | Unused import / undefined name in `app/` | Fix the reported file; do not skip the step |
| Import `app.main` fails | Settings or import-time error | Reproduce with the same CI env vars locally |
| Mapper configuration error | Broken relationship / missing model import | Fix models; ensure `app.models` registration path still loads |
| Migration fails on upgrade | Bad revision / DB not empty of conflicting objects | Inspect Alembic history; reset only disposable CI/test DBs |
| pytest collection aborts | Test DB safety guard | See README Testing + `app/tests/database.py` |
| Drift check fails | Model change without migration | Add a real Alembic revision; delete any scratch file |
| `uv sync --locked` fails | Lockfile out of date with `pyproject.toml` | Run `uv lock` locally and commit `uv.lock` |

## Why the workflow YAML is not unit-tested

GitHub Actions YAML is infrastructure executed by GitHub’s runner service.
Unit-testing it inside this pytest suite would either:

- mock the entire Actions runtime (low signal), or
- require a second orchestrator such as `act` plus Docker-in-Docker (heavy,
  flaky, and outside this CI-only milestone).

Validation for this foundation is therefore:

1. Keep the workflow minimal and declarative.
2. Document the local command mirror above.
3. Rely on the workflow run itself on every push/PR as the integration test.

Application code under `app/` remains covered by the existing pytest suite
(352+ tests); CI simply runs that suite in a clean environment.
