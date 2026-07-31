# VeriSure Ad Automation

Multi-platform automated advertisement deployment system.

Status:

Project initialization.

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
- Authentication (`get_current_user`) and authorization
  (`get_authorization_context`, `require_permission`) are never mocked or
  overridden; tests exercise the real JWT → session → repository → service
  → endpoint chain.
- A `super_admin` test creates its own `platform`-slugged tenant inside the
  rolled-back transaction; it is discarded at teardown like everything
  else and never touches any pre-existing platform tenant.