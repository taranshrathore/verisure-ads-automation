# VeriSure Ad Automation

Multi-platform automated advertisement deployment system.

Status:

Project initialization.

## Authorization status (read before touching auth/roles code)

The existing VeriSure CRM is intended to become the **single source of
truth for roles and permissions** across the CRM and this backend. A
duplicate, database-backed RBAC implementation (custom roles, role
assignments, system roles) previously existed in this repository and has
been **removed** — see `docs/HANDOFF.md` for the full migration record.

Current state:
- Authentication (JWT access tokens, refresh-token rotation) is unchanged
  and fully local to this backend.
- Authorization beyond authentication does **not exist right now**. `GET
  /api/v1/campaigns` currently enforces authentication only, with no
  permission check, as a documented temporary state.
- The CRM integration contract (how tokens are issued, how permissions are
  supplied — embedded claims vs. an authorization API, tenant/user ID
  mapping, revocation semantics) is **not yet defined in this repository**
  and must not be guessed at. See `docs/HANDOFF.md` for the exact list of
  open questions for the CRM team.
- **Frontend-supplied role or permission values must never be trusted
  directly by any endpoint.** Once CRM integration lands, authorization
  must be derived from the CRM-issued token/API, never from a client-sent
  field.

## Continuous Integration

Every push and pull request runs **Backend CI** on GitHub Actions (uv,
PostgreSQL service container, pyflakes, mapper check, Alembic, pytest, and
autogenerate drift detection). See [`docs/CI.md`](docs/CI.md) for the
pipeline, local mirror commands, and troubleshooting. CI uses test-safe
credentials only and does not deploy.

## Testing

The pytest suite (`app/tests/`) runs against a **dedicated test database**,
never the development database. This is enforced fail-closed: pytest
refuses to start at all unless it is configured correctly (see
`app/tests/database.py`).

### 1. Create the test database

Using the same PostgreSQL server started by `compose.yaml`:

```bash
psql "postgresql://verisure:change_me@localhost:5432/verisure_db" -c "CREATE DATABASE verisure_test_db"
```

### 2. Configure `TEST_DATABASE_URL`

Add it to your local `.env` (never committed), alongside `DATABASE_URL`:

```
TEST_DATABASE_URL=postgresql+psycopg://verisure:change_me@localhost:5432/verisure_test_db
```

The safety guard preventing accidental use of the development database
enforces, at pytest startup, that:

- `TEST_DATABASE_URL` is set at all (no fallback to `DATABASE_URL`);
- it is not identical to `DATABASE_URL`;
- its database name contains the substring `test` (case-insensitive).

If any check fails, pytest aborts immediately with a clear error and runs
no tests.

### 3. Run migrations against the test database

The application's own `alembic/env.py` always targets `DATABASE_URL`.
Point it at the test database for one command by overriding the
environment variable, then unsetting it again:

**PowerShell:**

```powershell
$env:DATABASE_URL = "postgresql+psycopg://verisure:change_me@localhost:5432/verisure_test_db"
uv run alembic upgrade head
Remove-Item Env:\DATABASE_URL
```

**bash/zsh:**

```bash
DATABASE_URL="postgresql+psycopg://verisure:change_me@localhost:5432/verisure_test_db" \
  uv run alembic upgrade head
```

Re-run this after adding new migrations. `Base.metadata.create_all()` is
never used as a substitute for migrations, including for tests.

### 4. Run the test suite

```bash
uv run pytest
```

### How it works

- `app/tests/database.py` builds a SQLAlchemy engine exclusively from
  `TEST_DATABASE_URL`; it never imports `app.database.session` (the
  application's `DATABASE_URL`-backed engine).
- Each test runs inside one outer transaction on a dedicated connection.
  The `get_db` dependency is overridden (the *only* overridden dependency)
  to yield a session bound to that same connection via
  `join_transaction_mode="create_savepoint"`, so application code's own
  `session.commit()` calls only release a SAVEPOINT. The outer transaction
  is rolled back at teardown unconditionally, so no row created by a test
  can survive it, even if the test fails or is interrupted.
- Authentication (`get_current_user`) is never mocked or overridden; tests
  exercise the real JWT → session → repository → endpoint chain. There is
  no authorization dependency to exercise beyond it right now — see
  "Authorization status" above.