# Deployment (Docker)

Production Docker Hardening for VeriSure Ads Automation. This document covers
local and production-oriented Compose usage only — not Kubernetes, Helm,
Terraform, or a specific cloud provider.

## What runs

| Service | Role | Notes |
| --- | --- | --- |
| `api` | FastAPI / uvicorn | Waits for Postgres, runs `alembic upgrade head`, then serves HTTP |
| `worker` | Publish worker | Waits for Postgres, starts `python -m app.orchestration.publish_worker` |
| `db` | PostgreSQL 17 | **Development compose only** |

API and worker are **separate containers** built from the same multi-stage
`Dockerfile`. Migrations run **only in the API container**.

## Files

| File | Purpose |
| --- | --- |
| `Dockerfile` | Multi-stage build, non-root runtime, no dev dependencies |
| `docker/start.sh` | Entrypoint: wait → (migrate if api) → process |
| `docker-compose.yml` | Local stack: api + worker + Postgres |
| `docker-compose.prod.yml` | Prod-oriented: api + worker (external database) |
| `compose.yaml` | Compatibility include of `docker-compose.yml` |
| `.dockerignore` | Keeps secrets, venvs, and tests out of the build context |

## Local startup

Prerequisites: Docker Desktop (or Engine + Compose v2).

1. Copy `.env.example` to `.env` and set at least:
   - `SECRET_KEY` or `JWT_SECRET_KEY` (Compose maps `SECRET_KEY` → `JWT_SECRET_KEY`)
   - `ENCRYPTION_KEY` (Fernet key; required before connecting providers)
   - Optional: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
2. Start the stack:

```bash
docker compose up --build
```

Equivalent (canonical file):

```bash
docker compose -f docker-compose.yml up --build
```

3. Verify liveness:

```bash
curl http://localhost:8000/api/v1/health/live
```

Expected: `{"status":"alive"}`.

Readiness (database + mappers):

```bash
curl http://localhost:8000/api/v1/health/ready
```

API listens on `http://localhost:8000` by default (`API_PORT` overrides the
host port). Postgres is published on `5432` (`POSTGRES_PORT` overrides).

### Local test database

The pytest suite still requires a **separate** database whose name contains
`test` (see README Testing). With Compose Postgres running:

```bash
docker exec -it verisure-postgres \
  psql -U verisure -d verisure_db \
  -c "CREATE DATABASE verisure_test_db"
```

Point `TEST_DATABASE_URL` at that database from the host (hostname
`localhost`), distinct from `DATABASE_URL`.

## Production startup

`docker-compose.prod.yml` does **not** start Postgres. Provide a managed or
external PostgreSQL URL.

```bash
export DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME"
export JWT_SECRET_KEY="replace-me"
export ENCRYPTION_KEY="replace-me"
export APP_ENV=production
export LOG_FORMAT=json

docker compose -f docker-compose.prod.yml up --build -d
```

Optional image tag override:

```bash
export VERISURE_IMAGE=verisure-ads-automation:prod
```

Stop:

```bash
docker compose -f docker-compose.prod.yml down
```

## Environment variables

| Variable | Used by | Notes |
| --- | --- | --- |
| `DATABASE_URL` | api, worker | SQLAlchemy URL (`postgresql+psycopg://...`). Required. |
| `SECRET_KEY` | compose mapping | Convenience alias; mapped to `JWT_SECRET_KEY` |
| `JWT_SECRET_KEY` | api, worker | Actual settings field for JWT signing |
| `ENCRYPTION_KEY` | api, worker | Fernet key(s) for provider credentials |
| `LOG_LEVEL` | api, worker | Default `INFO` |
| `LOG_FORMAT` | api, worker | `text` (dev compose) / `json` (prod compose) |
| `PUBLISH_JOB_POLL_INTERVAL_SECONDS` | worker | Must be ≥ 1 |
| `POSTGRES_USER` / `PASSWORD` / `DB` | db (dev) | Local Postgres only |
| `API_PORT` / `POSTGRES_PORT` | host publish | Host-side ports only |
| `APP_ENV` | api, worker | e.g. `development` / `production` |

Never bake secrets into the image. `.env` is excluded by `.dockerignore`.
Pass secrets via Compose `environment`, an env file, or the orchestrator.

## Migration behavior

1. Both containers wait until `DATABASE_URL` accepts `SELECT 1`.
2. **API only** runs `alembic upgrade head`.
3. Worker starts after the API becomes healthy (`/api/v1/health/live`), so the
   schema is already migrated.
4. Do **not** run migrations in the worker and API concurrently.

Manual migration (escape hatch):

```bash
docker compose exec api alembic upgrade head
```

## Health checks

- Compose **API** healthcheck hits `GET /api/v1/health/live` (no database).
- Application readiness remains available at `/api/v1/health/ready`.
- Worker has no HTTP listener; Compose restarts it on process failure
  (`restart: unless-stopped`). Worker capability can be inspected via
  `GET /api/v1/health/worker` on the API.

## Image details

- Multi-stage build with `uv sync --locked --no-dev`
- Runtime image: `python:3.14-slim-bookworm`
- Non-root user `verisure` (uid/gid `10001`)
- `PYTHONUNBUFFERED=1`
- Dev dependencies and `app/tests` are not installed in the runtime image

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `DATABASE_URL is required` | Missing env | Set `DATABASE_URL` (prod) or start with `docker-compose.yml` (dev) |
| `database_not_ready` | Postgres down / wrong host | Wait for `db` healthy; use hostname `db` inside Compose |
| API unhealthy / start_period exceeded | Migration failure or JWT missing | `docker compose logs api`; ensure `JWT_SECRET_KEY`/`SECRET_KEY` set |
| Worker exits on import | Settings validation | Same secrets as API; check `docker compose logs worker` |
| Host tests can't connect | Using Compose DB URL with host `db` | From the host use `localhost` and published port |
| Permission errors in container | Running as non-root | Do not write to system paths; app is read-mostly |
| Stale schema | Image/code ahead of DB | Restart API (re-runs `alembic upgrade head`) |

## Out of scope

This milestone does not include Kubernetes, Helm, Terraform, nginx, Redis,
cloud-specific deploy pipelines, or application business-logic changes.
