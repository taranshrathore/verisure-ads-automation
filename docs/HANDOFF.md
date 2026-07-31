# VeriSure Ad Automation — Engineering Handoff

**Purpose of this document:** a complete, self-contained handoff so that a
fresh chat/agent with zero access to prior conversation history can continue
production development safely. Everything below was verified directly
against the repository's source code, migrations, configuration, and git
history at the time this document was written (no claim here is from
unverified memory). If the repository has changed since, re-verify before
trusting any specific detail — but the architectural patterns and
invariants described are meant to remain stable.

---

## 1. PROJECT OVERVIEW

**Product name:** VeriSure Ad Automation (`app_name` default: "VeriSure Ad
Automation"; package name in `pyproject.toml`: `verisure-ad-automation`).

**Business purpose:** a multi-platform automated advertisement deployment
system — a multi-tenant B2B SaaS backend intended to let tenant
organizations manage and automate advertising campaigns across multiple ad
platforms (Meta, Google, LinkedIn, Microsoft, TikTok, Amazon, Pinterest,
Snapchat, Reddit, X — inferred only from placeholder env vars in
`.env.example`; no adapter integration code exists yet beyond one abstract
base class stub).

**Current backend scope (what actually exists today):**
- Multi-tenant authentication (login, refresh-token rotation, logout,
  logout-all) — fully implemented and tested manually.
- A complete RBAC (role-based access control) persistence layer and
  authorization engine — fully implemented.
- Exactly one business/domain endpoint, `GET /api/v1/campaigns`, which is
  intentionally a stub that proves the authorization flow end-to-end and
  returns an empty list. **No campaign persistence, adapters, or
  ad-platform integration exists yet.**
- Numerous empty placeholder packages (`app/adapters`, `app/agents`,
  `app/config`, `app/deterministic`, `app/middleware`, `app/orchestration`,
  `app/registry`, `app/schemas`, `app/services` at the package level,
  `app/utils`) exist as scaffolding from initial project setup. Most contain
  only an empty or near-empty `__init__.py`. `app/adapters/base_adapter.py`
  contains one abstract class stub (`BaseAdapter(ABC)`) with no methods
  defined yet. **Treat these as unimplemented scaffolding, not as evidence
  of hidden functionality.**

**Technology stack (verified from `pyproject.toml` and imports):**
- Python ≥3.12 (`requires-python = ">=3.12"`; the local dev venv currently
  runs Python 3.14.2 — no `.python-version` file pins a specific version).
- **FastAPI** ≥0.141.1 — web framework.
- **SQLAlchemy** ≥2.0.51 — ORM, synchronous (no async engine anywhere).
- **Alembic** ≥1.18.5 — schema + data migrations.
- **psycopg[binary]** ≥3.3.4 — PostgreSQL driver (`postgresql+psycopg://`
  URLs).
- **PostgreSQL 17** (via `compose.yaml`, official `postgres:17` image).
- **pydantic-settings** ≥2.14.2 — environment-backed settings.
- **pyjwt** ≥2.13.0 — JWT access-token signing/verification.
- **pwdlib[argon2]** ≥0.3.0 — Argon2id password hashing.
- **uvicorn** ≥0.52.0 — ASGI server.
- Dev-only: **pytest** ≥9.1.1, **httpx** ≥0.28.1 and **httpx2** ≥2.9.1
  (both present; `httpx2` is required so Starlette's `TestClient` does not
  emit a `StarletteDeprecationWarning` — see §9).
- Dependency management: **uv** (`uv.lock` present; `[tool.uv]
  dev-dependencies` — note this uses uv's now-deprecated
  `dev-dependencies` key rather than `[dependency-groups] dev`; a `uv add`
  invocation prints a deprecation warning about this but still works).

**Development environment and commands (all verified working):**
```powershell
# Start PostgreSQL (reads POSTGRES_USER/PASSWORD/DB from .env)
docker compose up -d

# Install/sync dependencies
uv sync

# Run the app locally
uv run uvicorn app.main:app --reload

# Apply migrations to the development database (DATABASE_URL from .env)
uv run alembic upgrade head

# Run the test suite (requires TEST_DATABASE_URL — see §9)
uv run pytest
```
The repo's own `.venv` is used directly in this environment as
`.venv\Scripts\python.exe` / `.venv\Scripts\python.exe -m alembic|pytest`
(Windows/PowerShell); `uv run <cmd>` is the cross-platform equivalent.

---

## 2. CURRENT REPOSITORY STRUCTURE

```
Verisure-ads-automation/
├── alembic/
│   ├── env.py                          # reads settings.database_url only
│   └── versions/
│       ├── e1c130d4a242_create_tenant_and_user_tables.py
│       ├── eafc0d83cadb_add_refresh_token_table.py
│       ├── fd90462691b5_add_rbac_tables.py
│       └── b7c3e5a9d214_seed_rbac_catalog.py
├── alembic.ini
├── compose.yaml                        # postgres:17 service only
├── .env                                 # gitignored, local secrets/config
├── .env.example                         # committed template (out of sync
│                                         #  with actual Settings fields —
│                                         #  see §14)
├── pyproject.toml
├── uv.lock
├── README.md                            # has a "Testing" section (§9)
├── docs/
│   └── HANDOFF.md                       # this file
└── app/
    ├── main.py                          # FastAPI app composition root
    ├── core/
    │   ├── settings.py                  # pydantic-settings Settings
    │   ├── logging.py                   # configure_logging(), logger
    │   ├── constants.py                 # empty TODO placeholder
    │   ├── exceptions.py                # framework-agnostic exception hierarchy
    │   ├── exception_handlers.py        # FastAPI handlers mapping exceptions→HTTP
    │   ├── security/
    │   │   ├── password.py              # Argon2id hash/verify (pwdlib)
    │   │   ├── tokens.py                # opaque refresh-token gen + SHA-256 hash
    │   │   └── jwt.py                   # access-token create/decode (PyJWT)
    │   └── authorization/
    │       ├── __init__.py              # re-exports catalog + context
    │       ├── catalog.py               # PermissionSlug/BuiltInRoleSlug/SystemRoleSlug StrEnums
    │       ├── builtin_roles.py         # app-side source of truth for seed generation
    │       └── context.py               # AuthorizationContext (frozen dataclass)
    ├── database/
    │   ├── base.py                      # DeclarativeBase `Base`
    │   └── session.py                   # get_engine()/SessionFactory() (DATABASE_URL only)
    ├── models/
    │   ├── __init__.py                  # imports all models onto Base.metadata
    │   ├── mixins.py                    # UUIDPrimaryKeyMixin/TimestampMixin/SoftDeleteMixin
    │   ├── tenant.py
    │   ├── user.py
    │   ├── refresh_token.py
    │   ├── permission.py
    │   ├── role.py
    │   ├── role_permission.py
    │   ├── user_role_assignment.py
    │   └── system_role_assignment.py
    ├── repositories/
    │   ├── tenant_repository.py
    │   ├── user_repository.py
    │   ├── refresh_token_repository.py
    │   └── authorization_repository.py
    ├── services/
    │   ├── auth_service.py              # AuthService (owns commits)
    │   └── authorization_service.py     # AuthorizationService (read-only, never commits)
    ├── api/
    │   ├── dependencies.py              # get_db, get_current_user, get_authorization_context, etc.
    │   ├── authorization.py             # require_permission(), require_system_role()
    │   └── v1/
    │       ├── auth.py                  # /api/v1/auth/* router
    │       └── campaigns.py             # /api/v1/campaigns router (stub)
    └── tests/
        ├── __init__.py
        ├── database.py                  # dedicated TEST_DATABASE_URL engine + validation
        ├── conftest.py                  # pytest_configure guard + db_session/client/authorization_fixture
        └── test_campaigns_authorization.py  # 6 authorization tests
```

**Empty/unused scaffolding packages** (each is essentially just an
`__init__.py`, verified empty or near-empty): `app/adapters/` (has one
abstract stub, `BaseAdapter`), `app/agents/`, `app/config/`,
`app/deterministic/`, `app/middleware/`, `app/orchestration/`,
`app/registry/`, `app/schemas/`, `app/services/__init__.py` (note:
`app/services/` also contains the two real service modules above — the
package itself has no other content), `app/utils/`. These exist purely as
placeholders; do not assume any hidden logic lives in them.

**Ownership of each architectural layer:**
- `app/models/` — SQLAlchemy ORM table definitions only; no business logic,
  no queries beyond what `relationship()` needs.
- `app/repositories/` — data access only. **Never commit or roll back**
  (explicit convention, stated in every repository's docstring). Return
  ORM objects or scalar values; never raise domain exceptions.
- `app/services/` — orchestrate one or more repositories inside a single
  request's use case; **own all `session.commit()`/`rollback()` calls**;
  raise domain exceptions from `app/core/exceptions.py`.
- `app/api/` — FastAPI routers, Pydantic request/response models, and
  dependency wiring only. **No authorization or business logic in route
  bodies** — routes call a service method and translate the result to a
  response model; permission checks are declared as dependencies.
- `app/core/` — framework-agnostic building blocks (settings, security
  primitives, the exception hierarchy, the authorization type system).
  `app/core/authorization/` in particular has zero FastAPI or SQLAlchemy
  imports — it is pure Python.

---

## 3. APPLICATION ARCHITECTURE

**FastAPI application composition** (`app/main.py`):
1. `configure_logging()` is called once at import time.
2. `FastAPI(...)` app is constructed with `title=settings.app_name`,
   `docs_url="/docs"`, `redoc_url="/redoc"`.
3. `register_exception_handlers(app)` wires the custom exception → HTTP
   mapping (see below).
4. `CORSMiddleware` is added with `allow_origins=["*"]` — explicitly marked
   with a `# TODO: Restrict these CORS settings before deploying to
   production.` comment. **This is a known, intentional gap, not an
   oversight to silently fix without confirming intent.**
5. An `api_v1_router = APIRouter(prefix="/api/v1")` aggregates
   `auth.router` and `campaigns.router`, then is included on `app`.
6. Two unauthenticated utility endpoints: `GET /` (service identification)
   and `GET /health` (liveness).

**Settings and environment loading** (`app/core/settings.py`):
- `Settings(BaseSettings)` with `model_config = SettingsConfigDict(
  env_file=".env", extra="ignore")`.
- Fields: `app_name`, `app_env`, `debug`, `api_version`, `database_url:
  str | None`, `test_database_url: str | None` (test-only, see §9),
  `jwt_secret_key: str` (required, no default), `jwt_algorithm`,
  `jwt_access_token_expire_minutes`, `jwt_issuer`, `jwt_audience`.
- A module-level singleton `settings = get_settings()` is created at import
  time via an `lru_cache`d `get_settings()`. **Real environment variables
  take precedence over `.env` file values** (standard pydantic-settings
  behavior) — this is exploited deliberately by the Alembic-against-test-DB
  workflow in §9.
- The database URL is **not** validated at settings-import time; validation
  is deferred to `app/database/session.py`'s `get_engine()` so the app can
  still start before PostgreSQL is configured.

**Database engine and session lifecycle** (`app/database/session.py`):
- `get_engine()` — `lru_cache`d, lazily calls `create_engine(
  settings.database_url)`; raises `RuntimeError` if `DATABASE_URL` is unset,
  **only when first called**, not at import time.
- `SessionFactory()` — `lru_cache`d `sessionmaker(bind=get_engine(),
  autocommit=False, autoflush=False, class_=Session)`.
- Request-scoped session: `app/api/dependencies.py::get_db()` is a
  generator dependency: `session = SessionFactory()(); yield session;
  finally: session.close()`. It never commits — every commit is owned by a
  service.
- No async engine/session exists anywhere (explicit `# TODO` acknowledging
  this in `session.py`).

**SQLAlchemy conventions:**
- SQLAlchemy 2.x typed declarative style throughout: `Mapped[...]`,
  `mapped_column(...)`, `DeclarativeBase` (`app/database/base.py`).
- UUID primary keys everywhere via `UUIDPrimaryKeyMixin`
  (`sqlalchemy.dialects.postgresql.UUID(as_uuid=True)`, Python-side
  `default=uuid.uuid4`).
- `TimestampMixin` — `created_at`/`updated_at`, both `server_default=
  func.now()`, `updated_at` also has `onupdate=func.now()`.
- `SoftDeleteMixin` — nullable `deleted_at`; **no query-level filtering or
  delete behavior is implemented anywhere** (explicit TODO in
  `mixins.py`) — every place that cares about soft-deletion filters
  `deleted_at.is_(None)` manually and explicitly (e.g.
  `AuthorizationRepository`, `AuthService.login`).
- Every foreign key and constraint in every model has an explicit,
  human-readable `name=` — no unnamed constraints exist. This is a
  deliberate convention; **new constraints must follow it.**
- `relationship(..., lazy="selectin")` is the default loading strategy used
  throughout (avoids N+1 without needing explicit `joinedload()` calls at
  every call site).

**Repository/service/API boundaries:** see §2 "Ownership" above — this is
the load-bearing convention of the codebase and is enforced by explicit
docstrings in nearly every file ("Does not commit or roll back", "Strictly
read-only", etc.).

**Dependency-injection flow** (`app/api/dependencies.py`,
`app/api/authorization.py`): FastAPI `Depends()` chains, layered as:
```
get_db()
  → get_tenant_repository(db) / get_user_repository(db) /
    get_refresh_token_repository(db) / get_authorization_repository(db)
      → get_auth_service(...) / get_authorization_service(...)

oauth2_scheme (Bearer) → get_current_user(token, tenant_repository, user_repository)
  → get_authorization_context(current_user, authorization_service)
    → require_permission(slug) / require_system_role(slug)   [app/api/authorization.py]
```
FastAPI's per-request dependency cache guarantees `get_db`,
`get_current_user`, and `get_authorization_context` each execute **at most
once per request**, no matter how many times they are depended upon.

**Logging** (`app/core/logging.py`): `configure_logging()` calls
`logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s |
%(levelname)s | %(message)s")`; a single named logger `logger =
logging.getLogger("verisure")` is exported and reused everywhere (e.g.
`AuthorizationService`'s security-alert and unknown-slug warnings).

**Exception architecture** (`app/core/exceptions.py` +
`app/core/exception_handlers.py`):
- A framework-agnostic hierarchy rooted at `AppError(Exception)`, with a
  default message set in `__init__`. Subtrees: `AuthenticationError`
  (`InvalidCredentialsError`, `InvalidAccessTokenError`,
  `InvalidRefreshTokenError`, `RefreshTokenExpiredError`,
  `RefreshTokenRevokedError`, `RefreshTokenReuseError`) and
  `AuthorizationError` (`PermissionDeniedError`, `CrossTenantAccessError`).
  Standalone: `TenantNotFoundError`, `TenantInactiveError`,
  `UserNotFoundError`, `UserInactiveError`, `RoleNotFoundError`,
  `PermissionNotFoundError`, `RoleAssignmentConflictError`,
  `ProtectedRoleError`, `LastTenantAdminError`. **Some of these
  (`RoleNotFoundError`, `PermissionNotFoundError`,
  `RoleAssignmentConflictError`, `ProtectedRoleError`,
  `LastTenantAdminError`) are pre-declared for the *next* milestone
  (RoleManagementService) and are not yet raised anywhere** — verified by
  their absence from any `raise` statement outside `exceptions.py` itself.
- `register_exception_handlers(app)` registers one `@app.exception_handler`
  per exception type (not per-hierarchy-root, so subclasses need their own
  registration or they fall through to FastAPI's default 500 handler —
  **note:** `AuthenticationError` itself *is* registered as a catch-all for
  its whole subtree since FastAPI dispatches by nearest matching registered
  type in the MRO, so `InvalidCredentialsError` etc. correctly hit the
  `AuthenticationError` handler → 401). Mapping: `AuthenticationError`→401
  (+`WWW-Authenticate: Bearer`), `TenantNotFoundError`→404,
  `TenantInactiveError`→403, `UserNotFoundError`→404,
  `UserInactiveError`→403, `PermissionDeniedError`→403,
  `CrossTenantAccessError`→404, `RoleNotFoundError`→404,
  `PermissionNotFoundError`→404, `RoleAssignmentConflictError`→409,
  `ProtectedRoleError`→403, `LastTenantAdminError`→409. All responses are
  `{"detail": <message>}`.

---

## 4. DATABASE AND MIGRATIONS

**Current tables** (8 total, all verified in models + migrations):

| Table | Key columns | Notes |
|---|---|---|
| `tenants` | `id` PK, `name`, `slug` unique, `created_at/updated_at/deleted_at` | Top-level tenancy boundary |
| `users` | `id` PK, `tenant_id` FK→tenants, `email`, `hashed_password`, `role` (flat string, TODO to replace with RBAC), timestamps, `deleted_at` | `UNIQUE(tenant_id, email)`; `UNIQUE(id, tenant_id)` exists solely to be the referenced side of a composite FK (see below) |
| `refresh_tokens` | `id` PK, `user_id` FK→users, `family_id`, `token_hash` unique, `replaced_by_token_id` FK→self, `revoked_at`, `expires_at`, timestamps | No raw token ever stored — only SHA-256 hash |
| `permissions` | `id` PK, `slug` unique, `description`, timestamps | Global, no tenant scope, no soft-delete |
| `roles` | `id` PK, `tenant_id` FK→tenants nullable, `slug`, `name`, `is_builtin` bool, timestamps, `deleted_at` | See constraints below |
| `role_permissions` | composite PK (`role_id`, `permission_id`), both FKs | Pure join table, no timestamps/soft-delete by design |
| `user_role_assignments` | `id` PK, `user_id`, `tenant_id` (composite FK, see below), `role_id` FK→roles, `assigned_by_user_id`/`revoked_by_user_id` FK→users, `revoked_at`, timestamps | Soft-revoked, never hard-deleted |
| `system_role_assignments` | `id` PK, `user_id` FK→users, `system_role` (CHECK-constrained string), `assigned_by_user_id`/`revoked_by_user_id`, `revoked_at`, timestamps | Cross-tenant, platform-only in practice |

**Important constraints and indexes (all named, verified):**
- `uq_users_tenant_id_email` — one email per tenant.
- `uq_users_id_tenant_id` — added specifically to be the referenced side of
  `fk_user_role_assignments_user_id_tenant_id_users`, a **composite foreign
  key** `(user_id, tenant_id) → users(id, tenant_id)`. This is the DB-level
  guarantee that a `user_role_assignments` row's `tenant_id` genuinely
  matches the assigned user's own `tenant_id` — it is impossible at the
  database level to assign a role to a user under the wrong tenant.
- `ck_roles_reserved_slug` — `tenant_id IS NULL OR slug NOT IN
  ('tenant_admin','manager','employee','viewer')` — a *custom* tenant role
  can never reuse a built-in slug.
- `ck_roles_scope_matches_builtin` — `(tenant_id IS NULL AND is_builtin IS
  TRUE) OR (tenant_id IS NOT NULL AND is_builtin IS FALSE)` — enforces the
  two valid states of a role row at the database level.
- `uq_roles_slug_builtin` — partial unique index on `slug WHERE tenant_id
  IS NULL` — one global slug per built-in role.
- `uq_roles_tenant_id_slug_custom` — partial unique index on `(tenant_id,
  slug) WHERE tenant_id IS NOT NULL AND deleted_at IS NULL` — one active
  slug per tenant for custom roles (a soft-deleted role's slug can be
  reused).
- `uq_user_role_assignments_user_id_role_id_active` — partial unique index
  `(user_id, role_id) WHERE revoked_at IS NULL` — a user cannot hold two
  simultaneous active assignments of the same role.
- `ix_user_role_assignments_user_id_tenant_id_active` — non-unique partial
  index `(user_id, tenant_id) WHERE revoked_at IS NULL` — the exact index
  `AuthorizationRepository`'s Q1/Q2 queries are designed to hit.
- `ck_system_role_assignments_system_role` — `system_role IN
  ('super_admin')` — only one system role slug exists today
  (`SystemRoleSlug.SUPER_ADMIN`); extending this requires an additive
  migration that alters this CHECK constraint.
- `uq_system_role_assignments_user_id_system_role_active` — partial unique
  index `(user_id, system_role) WHERE revoked_at IS NULL`.

**Tenant-isolation guarantees:**
1. Every tenant-scoped table carries an explicit `tenant_id` FK.
2. The `users(id, tenant_id)` unique constraint + the composite FK from
   `user_role_assignments` make cross-tenant role assignment **structurally
   impossible**, not just application-logic-prevented.
3. Built-in roles (`tenant_id IS NULL`) are global/shared by design — a
   change to a built-in role's permission set affects every tenant
   simultaneously. This trade-off was made explicitly and is documented in
   `role.py`'s docstring.
4. System roles (`system_role_assignments`) are cross-tenant by design but
   are **only supposed to be held by users of one specific "platform"
   tenant** (`PLATFORM_TENANT_SLUG = "platform"` in
   `app/core/authorization/catalog.py`). This is a read-time
   (`AuthorizationService.build_context`) fail-closed invariant today —
   **there is no write-time enforcement yet** because no
   `SystemRoleManagementService` exists (see §16).

**Soft-deletion behaviour:**
- `tenants`, `users`, `roles` have `deleted_at` (via `SoftDeleteMixin`).
  `permissions`, `role_permissions` do not (permissions are pure code-driven
  catalog data; `role_permissions` is a hard-deleted join row by design,
  per its docstring).
- **No automatic query filtering exists.** Every read path that needs to
  respect soft-deletion filters `deleted_at.is_(None)` explicitly:
  `AuthService.login` (tenant + user), `AuthorizationRepository`'s Q1/Q2
  (`Role.deleted_at.is_(None)`). If you add a new read path over a
  soft-deletable table, you must add this filter yourself — there is no
  session-level guard.
- `user_role_assignments` and `system_role_assignments` use **revocation**
  (`revoked_at`), not soft-deletion — this is deliberate, to preserve
  grant/revoke audit history on the row itself, per their docstrings.

**Alembic migration history, in exact order (verified via `alembic upgrade
head` output and file contents):**
1. `e1c130d4a242` — *create tenant and user tables* (`down_revision=None`,
   the root). Creates `tenants`, `users`.
2. `eafc0d83cadb` — *add refresh token table* (`down_revision=
   e1c130d4a242`). Creates `refresh_tokens`.
3. `fd90462691b5` — *add rbac tables* (`down_revision=eafc0d83cadb`).
   Adds `uq_users_id_tenant_id` to `users`; creates `permissions`, `roles`
   (with both CHECK constraints and both partial unique indexes),
   `role_permissions`, `user_role_assignments` (with the composite FK),
   `system_role_assignments`.
4. `b7c3e5a9d214` — *seed rbac catalog* (`down_revision=fd90462691b5`,
   current head). A **pure data migration** — inserts the frozen literal
   snapshot described below. Explicitly documented as never importing
   `app.core.authorization` (an applied migration is not a runtime
   synchronizer); idempotent via `ON CONFLICT DO NOTHING` and slug-based
   subselects for the join table.

**Seeded catalog data (from `b7c3e5a9d214`, verified literal contents):**
- 6 permissions: `users:read`, `users:manage`, `roles:read`,
  `roles:manage`, `campaigns:read`, `campaigns:manage` (fixed literal
  UUIDs, see the migration file for exact values).
- 4 built-in roles (`tenant_id IS NULL`, `is_builtin=TRUE`): `tenant_admin`,
  `manager`, `employee`, `viewer` (fixed literal UUIDs).
- Role→permission grants: `tenant_admin` gets all 6 permissions; `manager`
  gets `users:read`, `roles:read`, `campaigns:read`, `campaigns:manage`;
  `employee` gets `campaigns:read`, `campaigns:manage`; `viewer` gets only
  `campaigns:read`.
- **No tenants, no users, no credentials, no assignments are seeded** —
  explicitly stated in the migration's module docstring.

**Platform-tenant assumptions:**
- `PLATFORM_TENANT_SLUG = "platform"` is defined in
  `app/core/authorization/catalog.py` as the single source of truth for
  this slug.
- **No migration creates a `platform` tenant row.** It does not exist in
  the development database today (verified: the seed migration seeds no
  tenants at all). Any developer/production bootstrap of a real
  `super_admin` user requires manually creating a `platform`-slugged tenant
  and its first user — **no tooling for this exists yet.**
- The test suite creates its own throwaway `platform`-slugged tenant inside
  a rolled-back transaction for the `super_admin` test case (see §9) — this
  never touches the development or any persistent database.

---

## 5. AUTHENTICATION

**Password hashing** (`app/core/security/password.py`): Argon2id via
`pwdlib.PasswordHash.recommended()`. `hash_password(password) -> str`,
`verify_password(password, password_hash) -> bool`. No custom Argon2
parameters are set — whatever `pwdlib`'s "recommended" preset currently is.

**Access-token implementation** (`app/core/security/jwt.py`):
- `create_access_token(user_id, tenant_id) -> str` — signs a JWT with
  claims `sub` (user id), `tenant_id`, `type="access"`, `jti` (random
  UUID), `iat`/`nbf`/`exp` (from `settings.jwt_access_token_expire_minutes`,
  default 15 min), `iss`/`aud` from settings. Algorithm from
  `settings.jwt_algorithm` (default `HS256`), secret from
  `settings.jwt_secret_key`.
- `decode_access_token(token) -> dict` — validates signature, issuer,
  audience via PyJWT; additionally checks `claims["type"] == "access"` and
  raises `jwt.InvalidTokenError` otherwise (defends against a refresh-style
  token being reused as an access token — though refresh tokens are opaque
  strings, not JWTs, so this specific check is currently defensive/vestigial
  but cheap).
- Stateless: the JWT itself is never persisted. Every request still hits
  the database to re-verify the tenant and user are still active (see
  `get_current_user` below) — deliberately, to prevent a still-valid,
  unexpired JWT from granting access after deletion/deactivation
  (documented TODO acknowledges the extra query cost and defers caching).

**Refresh-token implementation** (`app/core/security/tokens.py` +
`app/models/refresh_token.py` + `app/repositories/refresh_token_repository.py`
+ `app/services/auth_service.py`):
- Opaque, not a JWT: `generate_refresh_token() ->
  secrets.token_urlsafe(32)`.
- **Token storage and hashing:** only `hash_token(raw) ->
  hashlib.sha256(raw).hexdigest()` is persisted, in
  `refresh_tokens.token_hash` (unique). The raw token is returned to the
  client once and never stored anywhere server-side.
- **Refresh rotation:** `AuthService.refresh(raw_refresh_token)` looks up
  the token by hash; if valid (not reused, not revoked, not expired),
  creates a brand-new `RefreshToken` row (new id, same `family_id`), sets
  `replaced_by_token_id` on the old row, and revokes the old row —
  atomically inside one `session.commit()`.
- **Reuse detection:** if the looked-up token already has
  `replaced_by_token_id IS NOT NULL` (i.e. it was already rotated once
  before), the *entire token family* is revoked
  (`refresh_token_repository.revoke_family(family_id)`) and
  `RefreshTokenReuseError` is raised — this is the standard "refresh-token
  family revocation on reuse" pattern, protecting against a stolen/replayed
  refresh token.
- **Logout/revocation:** `AuthService.logout(raw_refresh_token)` revokes
  exactly one token by hash. `AuthService.logout_all(tenant_id, user_id)`
  revokes every currently-active token for that user
  (`revoke_all_for_user`).
- A documented, **not-yet-fixed** TOCTOU race exists in `refresh()`:
  concurrent refresh requests using the same token could both read it as
  valid before either commits, causing two rotations from one parent. The
  code has an explicit `# TODO` proposing either a `SELECT ... FOR UPDATE`
  row lock or an atomic conditional `UPDATE ... WHERE ... AND revoked_at IS
  NULL` with an affected-row-count check. **This is known technical debt,
  not a design decision to preserve.**

**Authentication repositories, services, dependencies, and endpoints:**
- Repositories: `TenantRepository.get_by_id/get_by_slug`,
  `UserRepository.get_by_id/get_by_tenant_and_email`,
  `RefreshTokenRepository.create/get_by_token_hash/mark_replaced/revoke/
  revoke_family/revoke_all_for_user`. All read-only w.r.t. transactions (no
  commits).
- Service: `AuthService` (constructor takes the three repositories + the
  raw `Session`, since it alone needs to call `session.commit()`).
  `_REFRESH_TOKEN_LIFETIME = timedelta(days=30)` is hardcoded (documented
  TODO to move to settings).
- Dependencies (`app/api/dependencies.py`): `oauth2_scheme =
  OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")`; `get_current_user`
  decodes the JWT, validates claims, then does two DB lookups (tenant, then
  user) and raises 401 via a local `_unauthorized()` helper on any failure
  (a narrow `except` tuple, not a blanket `except Exception`).
- Endpoints (`app/api/v1/auth.py`, all under `/api/v1/auth`):
  - `POST /login` — body `{tenant_slug, email, password}` → `200
    {access_token, refresh_token}`.
  - `POST /refresh` — body `{refresh_token}` → `200 {access_token,
    refresh_token}`.
  - `POST /logout` — body `{refresh_token}` → `204` (no body).
  - `POST /logout-all` — requires a valid access token (`get_current_user`)
    → `204` (no body).

**Security invariants (verified as currently implemented):**
- Uniform `InvalidCredentialsError` on **any** login failure (bad tenant,
  bad email, inactive tenant/user, bad password) — deliberately restored
  after an earlier iteration used per-cause exceptions, specifically to
  prevent tenant/user-existence enumeration via error messages (verified
  from git history and current `login()` code — every failure path raises
  the same exception type with the same default message).
- Every other auth failure path (`refresh`, `logout`, `logout_all`) *does*
  use specific exceptions (`InvalidRefreshTokenError`,
  `RefreshTokenExpiredError`, `RefreshTokenRevokedError`,
  `RefreshTokenReuseError`, `UserNotFoundError`) since there is no
  equivalent enumeration risk once a caller already holds a bearer
  credential.
- No raw refresh token is ever persisted (only its SHA-256 hash).
- `get_current_user` re-validates tenant/user existence and activity on
  every request (no trust in JWT claims beyond signature/expiry).

---

## 6. RBAC PERSISTENCE

Covered in detail in §4 (tables/constraints). Summary of model
responsibilities:
- `Tenant`, `User` — pre-existing (not part of the RBAC phase but
  RBAC-relevant via the `(id, tenant_id)` composite-FK anchor).
- `Permission` — global, code-defined capability; no tenant scope, no
  soft-delete.
- `Role` — either a global built-in role (`tenant_id IS NULL,
  is_builtin=True`) or a tenant-owned custom role
  (`tenant_id IS NOT NULL, is_builtin=False`); soft-deletable.
- `RolePermission` — pure join row, hard-deleted, no audit trail (audit
  trail lives on `UserRoleAssignment` instead).
- `UserRoleAssignment` — tenant-scoped grant of one role to one user;
  soft-revoked via `revoked_at`; composite FK to `users(id, tenant_id)`
  guarantees tenant consistency at the DB level; `assigned_by_user_id`/
  `revoked_by_user_id` for audit.
- `SystemRoleAssignment` — cross-tenant grant of one system role to one
  user; soft-revoked; `CHECK (system_role IN ('super_admin'))` currently
  allows exactly one value.

**Built-in roles:** `tenant_admin`, `manager`, `employee`, `viewer` (see §4
seed data for exact permission grants). Defined as an enum
(`BuiltInRoleSlug`) in `app/core/authorization/catalog.py`, with
display-name/permission-set mappings in
`app/core/authorization/builtin_roles.py` — **this module is the
application-side source of truth used only to *generate* migrations; the
committed migration (`b7c3e5a9d214`) contains its own independent literal
snapshot and does not import it.** If you change
`BUILTIN_ROLE_PERMISSIONS`/`BUILTIN_ROLE_NAMES` in `builtin_roles.py`, you
must **also** write a brand-new additive migration to apply the change to
real databases — editing `builtin_roles.py` alone changes nothing at
runtime.

**Permission catalog:** `PermissionSlug` StrEnum in
`app/core/authorization/catalog.py` — currently exactly 6 members
(`USERS_READ`, `USERS_MANAGE`, `ROLES_READ`, `ROLES_MANAGE`,
`CAMPAIGNS_READ`, `CAMPAIGNS_MANAGE`), matching the seeded `permissions`
table 1:1. `PERMISSION_DESCRIPTIONS` maps each to its human-readable
description (also matching the seed migration's `description` column
values).

**Composite foreign keys and CHECK constraints:** see §4 — the
`user_role_assignments (user_id, tenant_id) → users (id, tenant_id)`
composite FK and the two `roles` CHECK constraints are the two most
architecturally significant DB-level invariants in the RBAC schema.

**Revocation and soft-deletion semantics:** `UserRoleAssignment`/
`SystemRoleAssignment` use `revoked_at` (never hard-deleted, audit
preserved). `Role` uses `deleted_at` (soft-delete). Both are treated
identically by the authorization read path: a role with `deleted_at IS NOT
NULL` OR an assignment with `revoked_at IS NOT NULL` contributes nothing to
a user's effective permissions (`AuthorizationRepository`'s Q1/Q2 filter
both).

---

## 7. AUTHORIZATION ENGINE

**`PermissionSlug` / `BuiltInRoleSlug` / `SystemRoleSlug`**
(`app/core/authorization/catalog.py`) — three `StrEnum` catalogs (Python
3.11+ `enum.StrEnum`). Using enum members at call sites (rather than raw
strings) turns a typo into an `AttributeError` at import time instead of a
silently-always-denying nonexistent permission string. `PLATFORM_TENANT_SLUG
= "platform"` also lives here as the single source of truth for the
platform-tenant slug.

**`AuthorizationContext`** (`app/core/authorization/context.py`) — a
`@dataclass(frozen=True)` with fields `user_id: UUID`, `tenant_id: UUID`,
`permissions: frozenset[PermissionSlug]`, `tenant_roles: frozenset[str]`,
`system_roles: frozenset[str]`. Pure Python — no ORM objects, no session,
no FastAPI imports. Methods: `is_super_admin` (property, checks
`SystemRoleSlug.SUPER_ADMIN in system_roles`), `has_permission(permission)`
(**the sole canonical location of the super_admin bypass** — returns `True`
immediately if `is_super_admin`, else checks membership in `permissions`),
`has_all(*permissions)`/`has_any(*permissions)` (both delegate to
`has_permission`, inheriting the bypass automatically), `has_system_role(
system_role)` (**exact match only, never bypassed** — holding
`super_admin` does not imply holding some other hypothetical system role).

**`AuthorizationRepository`** (`app/repositories/authorization_repository.py`)
— three independent, individually-indexed, read-only queries, returning
plain `list[str]` slugs (never ORM objects):
- Q1 `get_active_tenant_role_slugs(user_id, tenant_id)` — distinct role
  slugs from every active (`revoked_at IS NULL`), non-soft-deleted role
  assignment.
- Q2 `get_effective_permission_slugs(user_id, tenant_id)` — distinct
  permission slugs granted transitively through those same active role
  assignments (inner joins are correct here, per its docstring, because a
  zero-permission role genuinely contributes nothing to this set).
- Q3 `get_active_system_role_slugs(user_id)` — active system-role
  assignment slugs, no tenant filter (system roles are cross-tenant by
  definition).
- **Design rationale (verified from the code's own docstring):** Q1 and Q2
  are deliberately *separate* queries rather than one combined join,
  specifically so that a role with zero permissions still shows up in
  `tenant_roles` — a combined inner join would silently omit it.

**`AuthorizationService`** (`app/services/authorization_service.py`) —
strictly read-only (never commits, never assigns/revokes — explicit in its
module docstring, which also states that future write-side services own
those responsibilities).
- `build_context(user_id, tenant_id, is_platform_tenant) ->
  AuthorizationContext` — calls all three repository queries, then:
  - **Platform-tenant invariant (read-side, fail-closed):** if
    `system_role_slugs` is non-empty but `is_platform_tenant` is `False`
    (i.e. corrupted data — a customer-tenant user somehow holds a system
    role), it logs `logger.critical("SECURITY ALERT: ...")` with the user
    id, tenant id, and offending slugs, and **discards** the system roles
    for this context (`system_role_slugs = []`). It never mutates the
    database — this is a read-time defense only.
  - **Unknown permission handling:** compares the raw DB permission slugs
    against `_KNOWN_PERMISSION_SLUGS = frozenset(slug.value for slug in
    PermissionSlug)`. Any slug not recognized by the *current code
    version* is logged via `logger.warning(...)` (listing user id and the
    sorted unknown slugs) and **silently discarded** — never stored in the
    resulting `AuthorizationContext.permissions`. This protects against a
    newer database seed containing permissions an older, not-yet-deployed
    code version doesn't know about.
  - Constructs and returns the frozen `AuthorizationContext`.
- `require(context, permission)` — raises `PermissionDeniedError()` unless
  `context.has_permission(permission)` — delegates entirely to the context;
  adds no logic of its own beyond the raise (i.e. the super_admin bypass is
  **not duplicated** here).
- `require_system_role(context, system_role)` — raises
  `PermissionDeniedError()` unless `context.has_system_role(system_role)` —
  never bypassed.

**Request-scoped context caching:** `get_authorization_context` (in
`app/api/dependencies.py`) is a normal FastAPI `Depends()` callable with no
explicit caching code — it relies entirely on **FastAPI's built-in
per-request dependency cache** (`use_cache=True` is the default), which
memoizes the result of any given dependency callable for the lifetime of
one request. This means `build_context` (three DB queries) runs **at most
once per request**, no matter how many `require_permission(...)`
dependencies a single route declares.

**`require_permission` and `require_system_role`**
(`app/api/authorization.py`) — dependency *factories* (a function that
returns a dependency callable, parameterized by the permission/role to
check). Each returned inner `dependency(...)` performs **zero database
queries** and **zero bypass logic** of its own; it takes the already-built
`AuthorizationContext` via `Depends(get_authorization_context)`, and the
already-constructed `AuthorizationService` via
`Depends(get_authorization_service)`, and calls
`authorization_service.require(context, permission)` (or
`require_system_role`), returning the context to the caller (so the
protected route can further use it if needed, e.g.
`GET /api/v1/campaigns` receives `context` this way even though it doesn't
currently use it beyond the check).

**Exact authorization flow from HTTP request to route execution** (for
`GET /api/v1/campaigns`, verified end-to-end):
1. FastAPI extracts the `Authorization: Bearer <token>` header via
   `oauth2_scheme` (`OAuth2PasswordBearer`).
2. `get_current_user(token, tenant_repository, user_repository)` decodes and
   validates the JWT (`decode_access_token` — signature, issuer, audience,
   expiry, `type == "access"`), extracts `tenant_id`/`user_id` claims,
   looks up the tenant (must exist, `deleted_at IS NULL`) and the user
   (must exist, `deleted_at IS NULL`) via two DB queries. Any failure →
   `HTTPException(401, ..., headers={"WWW-Authenticate": "Bearer"})`
   raised directly (not via the custom exception hierarchy — a deliberate
   local `_unauthorized()` helper).
3. `get_authorization_context(current_user, authorization_service)` calls
   `AuthorizationService.build_context(user_id=current_user.id,
   tenant_id=current_user.tenant_id, is_platform_tenant=
   current_user.tenant.slug == PLATFORM_TENANT_SLUG)`, which runs the three
   `AuthorizationRepository` queries and returns the frozen
   `AuthorizationContext` described above.
4. `require_permission(PermissionSlug.CAMPAIGNS_READ)`'s inner dependency
   calls `authorization_service.require(context, PermissionSlug.
   CAMPAIGNS_READ)`, which calls `context.has_permission(...)` (the
   canonical super_admin-bypass check) and raises `PermissionDeniedError()`
   on failure.
5. If `PermissionDeniedError` was raised, the registered exception handler
   converts it to `403 {"detail": "Permission denied."}`.
6. Otherwise, `list_campaigns(context)` executes and returns
   `CampaignListResponse(items=[])`.

---

## 8. IMPLEMENTED API ENDPOINTS

All endpoints verified directly from router source files.

| Method & Path | Auth required | Authorization required | Request model | Response model | Status |
|---|---|---|---|---|---|
| `GET /` | None | None | — | `dict[str, str]` (inline, not a Pydantic model) | Implemented — trivial identification payload |
| `GET /health` | None | None | — | `dict[str, str]` (inline) | Implemented — liveness only |
| `POST /api/v1/auth/login` | None (this *is* the credential exchange) | None | `LoginRequest {tenant_slug, email, password}` | `TokenResponse {access_token, refresh_token}` | Fully implemented |
| `POST /api/v1/auth/refresh` | None (refresh token itself is the credential) | None | `RefreshRequest {refresh_token}` | `TokenResponse {access_token, refresh_token}` | Fully implemented, including rotation + reuse detection |
| `POST /api/v1/auth/logout` | None (refresh token itself is the credential) | None | `LogoutRequest {refresh_token}` | `204 No Content` | Fully implemented |
| `POST /api/v1/auth/logout-all` | Bearer JWT (`get_current_user`) | None | — | `204 No Content` | Fully implemented |
| `GET /api/v1/campaigns` | Bearer JWT (via `require_permission`) | `PermissionSlug.CAMPAIGNS_READ` | — | `CampaignListResponse {items: list[dict]}` | **Intentional stub** — see below |

**`GET /api/v1/campaigns` in detail:** this is explicitly documented (in
its own module docstring) as **the first authorization integration
point** — it exists specifically to prove the end-to-end
`JWT → get_current_user → AuthorizationContext → require_permission →
endpoint` chain works before any real campaign persistence, repository, or
service exists. It is protected exclusively via
`Depends(require_permission(PermissionSlug.CAMPAIGNS_READ))` passed as the
route's own parameter (not a route-level `dependencies=[...]` list, though
either form is supported by `require_permission`'s design). The route body
contains **zero authorization logic** — it unconditionally returns
`CampaignListResponse(items=[])`. `items: list[dict]` is a deliberate
placeholder type with a `# TODO: Replace items with a real CampaignRead
schema once campaign persistence and business logic exist.` comment — it
is **not** a stand-in for fabricated/fake data; there genuinely is no
campaign table yet, so an empty list is the only correct response.

---

## 9. TESTING ARCHITECTURE

**pytest configuration:** `[tool.pytest.ini_options] testpaths =
["app/tests"]` in `pyproject.toml`. Currently exactly one test file exists:
`app/tests/test_campaigns_authorization.py` (6 tests total — the entire
suite).

**`TEST_DATABASE_URL`:** a dedicated `Settings.test_database_url: str |
None` field (added specifically for testing; the application itself never
reads it — only `app/tests/database.py` does). Configured locally in
`.env` (gitignored) as
`postgresql+psycopg://verisure:change_me@localhost:5432/verisure_test_db`,
and documented as a placeholder in `.env.example`.

**Dedicated test engine/session** (`app/tests/database.py`): builds its
`Engine`/`sessionmaker` exclusively from `resolve_test_database_url()` —
this module **never imports `app.database.session`** (the application's
own `DATABASE_URL`-backed engine), by design, so the test suite is
structurally incapable of accidentally sharing a connection pool with the
production code path.

**Fail-closed safeguards** — `resolve_test_database_url()` raises
`TestDatabaseConfigurationError` (caught by `pytest_configure` in
`conftest.py` and re-raised as `pytest.UsageError`, aborting pytest before
any test collection/run) if, in order:
1. `TEST_DATABASE_URL` is unset — **no fallback to `DATABASE_URL`.**
2. `TEST_DATABASE_URL == DATABASE_URL` exactly.
3. The database name (URL path component) does not contain the substring
   `"test"` (case-insensitive).
All three branches were manually verified to actually abort `pytest` with
exit code 4 and zero tests collected when triggered (by temporarily editing
`.env` and restoring it afterward).

**Alembic preparation command** (documented in README.md "Testing"
section and verified working): temporarily override the `DATABASE_URL`
environment variable (which takes precedence over `.env`'s value per
pydantic-settings) to point at the test database, run `alembic upgrade
head` (unmodified `alembic/env.py`, which always reads
`settings.database_url`), then unset the override:
```powershell
$env:DATABASE_URL = "postgresql+psycopg://verisure:change_me@localhost:5432/verisure_test_db"
uv run alembic upgrade head
Remove-Item Env:\DATABASE_URL
```
`Base.metadata.create_all()` is **never** used as a substitute — the test
database's schema (and seeded RBAC catalog) comes exclusively from the same
Alembic revisions as production.

**FastAPI dependency override:** `client` fixture (`app/tests/conftest.py`)
overrides **only** `app.api.dependencies.get_db` —
`app.dependency_overrides[get_db] = _override_get_db` — to yield the exact
same `db_session` object used by test setup code. **No other dependency is
ever overridden**: `get_current_user`, `get_authorization_context`,
`require_permission`, etc. all run their real, unmodified implementation
against this same session.

**Savepoint/transaction rollback strategy:** each test gets one
`engine.connect()` → `connection.begin()` (outer transaction) → `Session(
bind=connection, join_transaction_mode="create_savepoint")` — the current,
officially documented SQLAlchemy 2.0 recipe for "joining a Session into an
external transaction" (verified against the live SQLAlchemy 2.0 docs, which
note this recipe was "newly improved again in 2.0; event handlers to
'reset' the nested transaction are no longer required" — i.e. the
pre-2.0-era event-listener version some blog posts still show is obsolete
for this SQLAlchemy version). Application code (and the
`authorization_fixture` test-setup code) can freely call
`session.commit()`/`flush()` exactly as production code does — each commit
only releases a SAVEPOINT. Teardown (`finally:` block in the `db_session`
fixture) always does `session.close(); transaction.rollback();
connection.close()` **unconditionally**, so no row survives a test whether
it passed, failed, or raised. **Empirically verified** (not just asserted):
after a full test run, both the dev DB and test DB were queried directly
and showed zero leftover rows, including the throwaway `platform` tenant
created by the super_admin test case.

**Why authentication and authorization are not mocked:** by construction —
overriding only `get_db` means every other dependency in the chain
(`get_current_user` → `get_authorization_context` → `require_permission`)
resolves through its real implementation, hitting the real
`AuthorizationRepository` SQL queries against the real (test) database.
This was a hard requirement from the task that produced this test
infrastructure and is preserved in every subsequent change.

**Existing six authorization tests** (all in
`app/tests/test_campaigns_authorization.py`, all against
`GET /api/v1/campaigns`):
1. `test_unauthenticated_request_returns_401` — no bearer token → 401.
2. `test_authenticated_user_without_campaigns_read_returns_403` — valid
   user, zero role assignments → 403.
3. `test_authenticated_user_with_campaigns_read_succeeds` — active
   `viewer` role assignment → 200, `{"items": []}`.
4. `test_super_admin_succeeds_through_has_permission_bypass` — platform
   tenant + active `super_admin` system role, **zero tenant permissions**
   → 200 (proves the bypass, not a coincidental grant).
5. `test_revoked_role_assignment_no_longer_grants_access` — `viewer` role
   assigned but `revoked_at` set → 403.
6. `test_soft_deleted_role_no_longer_grants_access` — custom role granting
   `campaigns:read`, but the role itself is soft-deleted
   (`deleted_at` set) → 403.

**Current warning resolution involving `httpx2`:** Starlette's
`TestClient` (re-exported by `fastapi.testclient`) now prefers a newer
package, `httpx2`, over plain `httpx`, emitting
`StarletteDeprecationWarning: Using httpx with starlette.testclient is
deprecated; install httpx2 instead` if only `httpx` is present. This is a
genuine, current (2026) upstream ecosystem change (verified via Starlette's
own source and changelog), not a bug in this repository. **Resolved** by
adding `httpx2>=2.9.1` as a dev dependency (`httpx` is kept too, for
backward compatibility, per upstream's own non-breaking migration
guidance) — no application or test code changes were needed;
`fastapi.testclient.TestClient` auto-detects `httpx2` when installed.
Verified: a full test run afterward produces zero warnings.

**Known limitations (explicitly acknowledged, not defects):**
- Transactional isolation depends on every DB-touching code path going
  through `Depends(get_db)`. Any future code that opens an independent
  connection outside this dependency chain (e.g. a background task, a raw
  `psycopg` call, or a second engine) would **not** be covered by the
  rollback and would need its own isolation/cleanup strategy.
- The "database name contains `test`" safety heuristic is a simple string
  check on the URL path component; it does not inspect the actual
  host/server. Pointing `TEST_DATABASE_URL` at a same-named database on an
  unintended host (e.g. a shared staging server) would still pass this
  specific check — this was an accepted trade-off ("a conservative
  validation rule appropriate to the existing settings architecture"), not
  a full network-topology safeguard.
- Only one test file/module exists; there is no coverage yet for the auth
  endpoints (`/api/v1/auth/*`), for the refresh-token rotation/reuse logic,
  or for any repository in isolation. All of that is currently verified
  only via manual `curl`/script-based smoke testing during earlier
  development, not via the automated pytest suite.

---

## 10. PRODUCTION INVARIANTS

Rules that any future implementation **must not violate**, verified as
currently true and load-bearing:

1. **No authorization logic directly inside route bodies.** Every
   permission/role check happens in a `Depends(require_permission(...))`
   or `Depends(require_system_role(...))` dependency, declared at the route
   signature level. `GET /api/v1/campaigns`'s body contains zero
   conditionals related to permissions.
2. **No repository bypass from API routes.** Routes call services;
   services call repositories. No router file imports a repository class
   directly (verified: `app/api/v1/auth.py` and `app/api/v1/campaigns.py`
   import only services/dependencies, never
   `app.repositories.*` directly).
3. **No raw refresh-token storage.** Only `hash_token(raw)` (SHA-256) is
   ever written to `refresh_tokens.token_hash`.
4. **No silent test fallback to development DB.** `resolve_test_database_url()`
   raises immediately; there is no code path where a missing/invalid
   `TEST_DATABASE_URL` results in tests quietly running against
   `DATABASE_URL`.
5. **No `Base.metadata.create_all()` in migration-backed integration
   tests.** The test database schema comes exclusively from `alembic
   upgrade head`.
6. **No duplicated super-admin bypass.** The bypass exists in exactly one
   place — `AuthorizationContext.has_permission()`. `AuthorizationService.
   require()`, `has_all()`, `has_any()` all delegate to it rather than
   re-implementing an `is_super_admin` check. Do not add a second bypass
   check anywhere else (e.g. inside a future service or route) — extend
   `AuthorizationContext` instead if new bypass semantics are ever needed.
7. **No cross-tenant assignments.** Enforced at the DB level today via the
   `user_role_assignments (user_id, tenant_id) → users (id, tenant_id)`
   composite FK. Any new tenant-scoped assignment table should follow the
   same pattern.
8. **Unknown permission slugs are logged and discarded**, never stored in
   an `AuthorizationContext` and never silently treated as granted. This
   happens in exactly one place: `AuthorizationService.build_context()`.
9. **`AuthorizationContext` remains immutable** (`@dataclass(frozen=True)`,
   only `frozenset`/primitive fields, no ORM objects, no session
   reference). Do not add mutable state or lazy-loading to it.
10. **Migrations already applied must not be rewritten.** `e1c130d4a242`,
    `eafc0d83cadb`, `fd90462691b5`, and `b7c3e5a9d214` are frozen history.
    Any schema or seed-data change ships as a **new, additive** migration
    with `down_revision = 'b7c3e5a9d214'` (or whatever the current head is
    at the time). The seed migration in particular is explicitly documented
    as "not a runtime synchronizer" — do not make it import
    `app.core.authorization` even for a "small fix"; write a new migration
    instead.

Additional invariants worth preserving even though not explicitly listed by
the user (all verified as current, intentional design):
- Repositories never call `session.commit()`/`rollback()` — only services
  do.
- Every new FK/unique/check constraint gets an explicit, descriptive
  `name=`.
- `AuthorizationRepository` returns only scalar slug lists, never ORM
  `Role`/`Permission` objects, and makes no authorization decisions itself.
- System-role checks (`has_system_role`) are always exact-match, never
  bypassed by holding a different system role.

---

## 11. CODING CONVENTIONS

- **Naming:** `snake_case` for functions/variables/modules, `PascalCase`
  for classes, `SCREAMING_SNAKE_CASE` for module-level constants
  (`PLATFORM_TENANT_SLUG`, `PERMISSION_DESCRIPTIONS`,
  `_KNOWN_PERMISSION_SLUGS`). Private/internal names get a leading
  underscore (`_session`, `_json_error`, `_database_name`).
- **Type hints:** exhaustive, modern-Python style —
  `str | None` (not `Optional[str]`), `list[str]`, `frozenset[PermissionSlug]`,
  `dict[str, str]`. Every function signature, including private helpers,
  is fully annotated including return type.
- **SQLAlchemy 2.x style:** `Mapped[...]` + `mapped_column(...)` for every
  column; `select(...)` + `session.scalar(...)`/`session.scalars(...)` for
  queries (not the legacy `Query` object) **except** in `app/tests/
  conftest.py`, which uses `session.query(...)` for setup convenience — this
  is a deliberate, isolated exception for test code, not a pattern to
  copy into application code.
- **Pydantic conventions:** every request/response model sets
  `model_config = ConfigDict(extra="forbid")`. Response models with
  optional fields use `response_model_exclude_none=True` at the route
  decorator (see `auth.py`).
- **Exception handling:** raise from the `app/core/exceptions.py`
  hierarchy in services; never raise a bare `ValueError`/`Exception` for
  domain errors. `except (SpecificError1, SpecificError2) as exc:` — narrow
  tuples, never bare `except Exception` in security-relevant code
  (`get_current_user` is the canonical example). Every custom exception
  class has a sensible default message set via `__init__`.
- **Async/sync conventions actually used:** the entire stack is
  **synchronous** — sync SQLAlchemy engine/session, sync repository/service
  methods, sync FastAPI route handlers (`def`, not `async def`) everywhere
  verified. There is no `async def` anywhere in `app/`. Do not introduce
  async/await without first confirming this is an intentional
  architecture change (an async engine was explicitly deferred via a
  `# TODO` in `session.py`).
- **Import conventions:** absolute imports rooted at `app.` everywhere
  (`from app.core.settings import settings`), never relative imports
  (`from .settings import ...`). `TYPE_CHECKING`-guarded imports are used
  for forward-reference-only type hints in ORM models (e.g. `if
  TYPE_CHECKING: from app.models.user import User`).
- **Test conventions:** fixture-based (pytest, function-scoped by default),
  factory fixtures return a `make(...)` callable rather than fixed data
  (see `authorization_fixture`), assertions target HTTP status codes and
  exact JSON bodies (`assert response.json() == {"items": []}`), test names
  are full sentences describing the scenario
  (`test_soft_deleted_role_no_longer_grants_access`).
- **Documentation expectations:** every module, class, and public function
  has a docstring explaining *why*, not just *what* (e.g.
  `AuthorizationRepository`'s module docstring explains why Q1/Q2 are
  separate queries rather than one join). Inline comments are reserved for
  non-obvious rationale, security notes, or TODOs — not restating what the
  next line of code obviously does. This handoff document should be
  updated (not just left stale) if a described convention changes.

---

## 12. COMPLETED MILESTONES

In exact chronological order (verified via `git log --oneline`, oldest
first):

1. `ff1e7ca` — Add PostgreSQL development infrastructure (`compose.yaml`,
   `.env`).
2. `d58d7a6` — Add tenant and user database schema (models + first
   migration `e1c130d4a242`).
3. `8fe1db5` — Add refresh token persistence model.
4. `e22d49b` — Add password hashing utility (Argon2id via pwdlib).
5. `8a9b8fe` — Add refresh token utilities (opaque generation + SHA-256
   hashing).
6. `6f0b361` — Add JWT security utilities (access-token create/decode).
7. `aeb8209` — Add tenant repository.
8. `becc3d7` — Add user repository.
9. `938491c` — Add refresh token repository.
10. `e4fa778` — Implement authentication service (`AuthService`: login,
    refresh, logout, logout-all; transaction ownership).
11. `f8013b2` — Implement FastAPI dependency injection (`get_db`,
    `get_current_user`, repository/service providers).
12. `a64751e` — Implement authentication API (`/api/v1/auth/*` router).
13. `c997095` — Configure FastAPI application entrypoint (`app/main.py`
    composition, CORS, health/root endpoints).
14. `8316bce` — Refactor authentication to use custom exceptions (moved
    off generic `ValueError`).
15. `7c049ec` — Improve application exception hierarchy (`AppError` base
    class default messages, etc.).
16. `41822b3` — Centralize FastAPI exception handling (moved HTTP
    translation out of routers into `exception_handlers.py`).
17. `abc40f9` — Add RBAC exception handling (added the RBAC-specific
    exception types + their handler mappings, pre-emptively, ahead of the
    services that will raise them).
18. `6a81aaf` — Add RBAC persistence models (`Permission`, `Role`,
    `RolePermission`, `UserRoleAssignment`, `SystemRoleAssignment` +
    migration `fd90462691b5`).
19. `7c050d8` — Implement authorization engine (typed catalog,
    `AuthorizationContext`, `AuthorizationRepository`,
    `AuthorizationService`, seed migration `b7c3e5a9d214`, FastAPI wiring
    via `require_permission`/`require_system_role`).
20. `52fb010` — Add protected campaigns endpoint and isolated
    authorization tests (`GET /api/v1/campaigns`, the dedicated
    `TEST_DATABASE_URL`-based test infrastructure with savepoint
    isolation, the `httpx2` warning fix, and the README "Testing"
    section).

**Outcome as of the latest commit:** the working tree is clean (nothing
uncommitted); all 6 authorization tests pass; the app imports cleanly and
`configure_mappers()` succeeds; migrations apply cleanly to a fresh
database. This milestone (§20) is the one explicitly approved by the user
immediately before this handoff was requested.

---

## 13. INTENTIONALLY DEFERRED WORK

Distinguished from defects — these are scoped-out by explicit instruction
or explicit `# TODO`, not oversights:

- **`RoleManagementService`** (grant/revoke *tenant*-scoped roles) and
  **`SystemRoleManagementService`** (grant/revoke *system* roles,
  including owning the platform-tenant eligibility invariant on the write
  side) — explicitly deferred; referenced only in docstrings/TODOs across
  `authorization_service.py`, `user_role_assignment.py`,
  `system_role_assignment.py`. **This is the recommended next milestone**
  (see §16).
- **Campaign persistence, repository, service, and adapters** — the
  `GET /api/v1/campaigns` stub was explicitly scoped to prove the
  authorization chain only; no `Campaign` model/table/business logic exists.
- **Async I/O** — explicitly deferred (`# TODO` in `session.py`); the whole
  stack is synchronous today.
- **Refresh-token lifetime configuration** — hardcoded
  `_REFRESH_TOKEN_LIFETIME = timedelta(days=30)` in `auth_service.py`, with
  an explicit TODO to move it into `Settings`.
- **CORS restriction** — `allow_origins=["*"]` with an explicit
  pre-production TODO.
- **Connection pool tuning** — `create_engine(settings.database_url)` with
  no `pool_size`/`max_overflow`/`pool_pre_ping`, explicit TODO in
  `session.py`.
- **Soft-delete query-level filtering** — no session event listener or
  global filter exists; every call site must remember to filter
  `deleted_at.is_(None)` manually (explicit TODO in `mixins.py`).
- **Platform-tenant bootstrap tooling** — no script/migration creates the
  first `platform` tenant + first `super_admin` user in a real database;
  this must be designed and built (likely as part of, or just before,
  `SystemRoleManagementService`).
- **Auth-endpoint test coverage** (`/api/v1/auth/*`) and
  repository-level unit tests — not yet written; only the authorization
  chain has automated tests today.
- **Adapters/agents/orchestration/registry/etc. packages** — placeholder
  scaffolding for the eventual multi-platform ad automation business logic
  referenced in the product's business purpose; entirely unimplemented.

---

## 14. KNOWN TECHNICAL DEBT AND LIMITATIONS

Only issues genuinely present and verified in the repository:

1. **Refresh-token rotation TOCTOU race** (`auth_service.py::refresh`) —
   documented in code, not yet fixed. Two concurrent refresh calls with the
   same token can both pass validation before either commits.
2. **`.env.example` is out of sync with `Settings`** — it lists `SECRET_KEY`,
   `ENCRYPTION_KEY`, and dozens of ad-platform credential placeholders
   (Meta, Google, LinkedIn, etc.) that `app/core/settings.py`'s `Settings`
   class does not define/read at all, while *omitting* `JWT_SECRET_KEY` and
   its siblings which the app actually requires (these are only present in
   the real, gitignored `.env`, not documented in `.env.example` prior to
   this handoff's `TEST_DATABASE_URL` addition — verify current
   `.env.example` state before assuming it is complete). Treat
   `.env.example` as a legacy/aspirational template, not an accurate guide
   to what `Settings` actually needs.
3. **`uv`'s `dev-dependencies` key is deprecated** — `pyproject.toml` still
   uses `[tool.uv] dev-dependencies = [...]`; `uv` itself prints a
   deprecation warning recommending `[dependency-groups] dev` instead.
   Cosmetic today, but will eventually need migrating.
4. **No connection pool tuning** — default SQLAlchemy pool settings only;
   unverified under real production load.
5. **No automated auth-endpoint tests** — `/api/v1/auth/login|refresh|
   logout|logout-all` have zero pytest coverage; correctness was previously
   verified only via manual smoke scripts (now deleted) during earlier
   development.
6. **`TEST_DATABASE_URL` safety check is host-agnostic** — see §9's "Known
   limitations."
7. **Flat `role: str` column still exists on `User`** — a vestige of
   pre-RBAC design (`default="member"`), explicitly marked with a `# TODO:
   Replace the flat role column with a dedicated roles/permissions model`.
   It is **not read by any authorization logic** today (verified:
   `AuthorizationRepository`/`AuthorizationService` never reference
   `User.role`) — it is dead weight, not a competing authorization source,
   but its removal (or repurposing) has not been scheduled.
8. **No rate limiting, no audit logging beyond the security-alert `logger.
   critical` calls, no request-id/correlation-id middleware** — none of
   these exist yet; not previously in scope.

---

## 15. NEXT ROADMAP

Recommended implementation order (each should be its own small,
reviewable, tested commit or small set of commits — matching this
project's established pattern of one focused capability per commit):

1. **`RoleManagementService` + `SystemRoleManagementService`** (see §16 for
   full detail) — the next milestone. Enables actually assigning/revoking
   roles, which today can only be done by hand-crafting rows (as the test
   fixtures do).
2. **Role/permission read + management API endpoints** — `GET /roles`,
   `GET /permissions`, `POST /users/{id}/roles`, `DELETE
   /users/{id}/roles/{assignment_id}`, etc., built on top of #1, protected
   by `roles:read`/`roles:manage`.
3. **Platform-tenant bootstrap** — a one-time script or Alembic data
   migration (or a documented manual runbook) to create the `platform`
   tenant and its first `super_admin` user in real environments, since no
   tooling for this exists yet.
4. **Auth-endpoint and repository test coverage** — close the gap
   identified in §14.5, reusing the same `TEST_DATABASE_URL` +
   savepoint-isolation infrastructure already built.
5. **First real business domain: Campaign persistence** — a real
   `Campaign` model/migration/repository/service, replacing the
   `GET /api/v1/campaigns` stub's `items: list[dict]` with a proper
   `CampaignRead` schema, and adding create/update/delete endpoints guarded
   by `campaigns:manage`.
6. **Ad-platform adapter integration** — begin implementing
   `app/adapters/` concretely (Meta/Google/etc.), per the product's stated
   business purpose, once campaign persistence exists to anchor it to.
7. **Production hardening pass** — CORS restriction, connection pool
   tuning, refresh-token lifetime → settings, the TOCTOU fix, rate
   limiting, structured/correlation-id logging — bundle these once the
   above functional milestones are stable, not before.

---

## 16. NEXT IMMEDIATE MILESTONE

**Confirmed appropriate:** yes — `RoleManagementService` and
`SystemRoleManagementService` are the clear, already-signposted next step.
Verified from the actual repository: neither service file exists yet
(`app/services/` contains only `auth_service.py` and
`authorization_service.py`); the exception types they will need
(`RoleNotFoundError`, `PermissionNotFoundError`,
`RoleAssignmentConflictError`, `ProtectedRoleError`,
`LastTenantAdminError`) are already pre-declared in
`app/core/exceptions.py` and already wired to HTTP status codes in
`exception_handlers.py`, but are **not yet raised anywhere**; multiple
model docstrings explicitly defer specific invariants to these
not-yet-built services (`user_role_assignment.py`: "Enforce role/tenant
compatibility ... and last-tenant-administrator protection in
RoleManagementService"; `system_role_assignment.py`: "Restrict grant/revoke
of system roles to platform-administration flows once
RoleManagementService and system endpoints exist";
`authorization_service.py`'s module docstring: "Grants and revocations
belong to the future RoleManagementService (tenant roles) and
SystemRoleManagementService (system roles, which owns the platform-tenant
eligibility invariant on the write side)").

**Do not implement this milestone yet — this section is a detailed
proposal for discussion and approval, per the operating instructions in
§18.**

### Objective
Enable safe, auditable, tenant-isolated creation/assignment/revocation of
tenant-scoped roles (via `RoleManagementService`) and system roles (via
`SystemRoleManagementService`), enforcing every invariant currently only
described in docstrings/TODOs, and expose them through a minimal set of
authorization-protected API endpoints.

### Proposed files (not yet created — names indicative, subject to
discussion)
- `app/repositories/role_repository.py` — CRUD-ish data access for `Role`
  rows (create custom role, get by id/slug, list by tenant, soft-delete),
  read-only-w.r.t.-commit like every other repository.
- `app/repositories/role_permission_repository.py` (or fold into
  `role_repository.py`) — attach/detach permissions on a role.
- `app/repositories/user_role_assignment_repository.py` — create
  assignment, revoke assignment, list active assignments for a user/tenant,
  count active administrators for a tenant (needed for last-admin
  protection).
- `app/repositories/system_role_assignment_repository.py` — analogous, for
  `system_role_assignments`.
- `app/services/role_management_service.py` — `RoleManagementService`
  (tenant-scoped: create/update/soft-delete custom roles, attach/detach
  permissions, assign/revoke `UserRoleAssignment`s).
- `app/services/system_role_management_service.py` —
  `SystemRoleManagementService` (assign/revoke `SystemRoleAssignment`s;
  owns the platform-tenant eligibility check on the write side).
- `app/api/v1/roles.py` — new router, likely mounted at
  `/api/v1/roles` and/or `/api/v1/users/{user_id}/roles`.
- New Pydantic schemas (`RoleRead`, `RoleCreate`, `RoleAssignmentRead`,
  etc.) — likely inline in `app/api/v1/roles.py` following this project's
  existing convention of colocating request/response models with their
  router (as `auth.py` and `campaigns.py` both do) rather than a separate
  `app/schemas/` module (even though an empty `app/schemas/` package
  already exists — decide during design discussion whether to finally use
  it or keep the existing colocation convention).
- A new additive Alembic migration **only if** any new constraint/index is
  found necessary during design (e.g. a partial-unique index needed for
  the last-tenant-administrator advisory-lock strategy, referenced but
  never implemented in earlier design discussion per the conversation
  history — must be re-confirmed with the user since this handoff cannot
  access that prior design detail directly).

### Service responsibilities
- `RoleManagementService`:
  - Create a custom role for a tenant (validating the slug doesn't collide
    with a reserved built-in slug — the DB CHECK constraint
    `ck_roles_reserved_slug` already backstops this, but the service should
    give a clean `RoleAssignmentConflictError`/validation error rather than
    letting a raw `IntegrityError` bubble up).
  - Attach/detach permissions on a custom role (never on a built-in role —
    raise `ProtectedRoleError` if attempted).
  - Soft-delete a custom role (never a built-in role — `ProtectedRoleError`).
  - Assign a role to a user **within the caller's own tenant only** (raise
    `CrossTenantAccessError` or similar if the target user belongs to a
    different tenant than the acting caller/role).
  - Revoke a role assignment, enforcing **last-tenant-administrator
    protection**: if the role being revoked is (or grants) the tenant's
    sole remaining "administrator" capability, raise
    `LastTenantAdminError` instead of allowing the revoke. (The exact
    definition of "administrator capability" — e.g. holding
    `roles:manage`, or specifically the `tenant_admin` built-in role — needs
    explicit confirmation with the user before implementation; do not
    assume.)
  - Enforce **subset delegation**: a caller assigning/attaching permissions
    must already hold `roles:manage` **and** must already effectively hold
    every permission they are attempting to grant (preventing
    privilege escalation via role management). This was a previously
    designed invariant (referenced in this project's history) that has not
    yet been implemented in code — confirm it is still wanted before
    building it.
- `SystemRoleManagementService`:
  - Assign a system role to a user, but **only if that user belongs to the
    platform tenant** (`user.tenant.slug == PLATFORM_TENANT_SLUG`) —
    raising a clear error (likely a new, more specific exception than the
    generic ones currently declared) otherwise. This is the **write-side**
    counterpart to `AuthorizationService.build_context`'s read-side
    fail-closed check — today only the read side exists.
  - Revoke a system role assignment.
  - Likely requires the caller to already hold `super_admin` themselves
    (self-referential bootstrapping problem for the *very first*
    `super_admin` — needs a bootstrap-tooling answer, see §15 item 3,
    before this can be fully self-service).

### Repository additions
See "Proposed files" above. All new repository methods must follow the
existing convention: take a `Session` in `__init__`, never call
`commit()`/`rollback()`, return ORM objects or scalars, raise no domain
exceptions (validation/business-rule enforcement belongs in the service
layer, not the repository).

### Authorization requirements
- All new `roles.py` endpoints must be protected via
  `Depends(require_permission(PermissionSlug.ROLES_READ))` or
  `Depends(require_permission(PermissionSlug.ROLES_MANAGE))` as
  appropriate — **no new authorization mechanism**; reuse
  `require_permission`/`require_system_role` exactly as
  `campaigns.py` does.
- System-role assignment endpoints (if exposed as HTTP endpoints at all in
  this milestone, vs. being admin-tooling-only) must be protected via
  `Depends(require_system_role(SystemRoleSlug.SUPER_ADMIN))`.

### Validation rules (to confirm with user before implementing)
- Subset-delegation enforcement (see above) — confirm still wanted.
- Exact definition of "last tenant administrator" for the protection rule.
- Whether custom-role permission attachment should be restricted to only
  permissions already known to `PermissionSlug` (recommended: yes, reject
  unknown slugs at the API boundary with a 422, consistent with the
  "typed identifiers" design principle already used everywhere else).

### Tenant and platform invariants
- Every `RoleManagementService` operation must be scoped to exactly one
  tenant (the caller's own, taken from their `AuthorizationContext.
  tenant_id` — never a value taken from the request body/path without
  cross-checking it matches the authenticated tenant, to prevent
  parameter-tampering cross-tenant access).
- `SystemRoleManagementService` must re-verify platform-tenant membership
  at write time (do not trust a cached `AuthorizationContext` for this —
  re-query, or accept a freshly loaded `User`/`Tenant`).

### Expected endpoints, if appropriate (proposed, not final)
- `GET /api/v1/roles` — list roles visible to the tenant (built-in +
  tenant's own custom roles) — `roles:read`.
- `POST /api/v1/roles` — create a custom role — `roles:manage`.
- `PATCH /api/v1/roles/{role_id}` / `DELETE /api/v1/roles/{role_id}`
  (soft-delete) — `roles:manage`.
- `POST /api/v1/users/{user_id}/roles` — assign a role — `roles:manage`.
- `DELETE /api/v1/users/{user_id}/roles/{assignment_id}` — revoke — `roles:manage`.
- System-role endpoints: TBD — may be deliberately excluded from the HTTP
  API entirely in this milestone (admin-tooling/CLI-only) pending user
  input, given the bootstrap chicken-and-egg problem noted above.

### Required tests
- Service-level tests (new, e.g. `test_role_management_service.py`) using
  the same `TEST_DATABASE_URL` + savepoint-isolation infrastructure as
  `app/tests/conftest.py`, extending `authorization_fixture` or adding
  sibling fixtures as needed — covering: successful role creation/
  assignment/revocation; rejection of built-in role modification
  (`ProtectedRoleError`); rejection of cross-tenant assignment; rejection
  of last-administrator removal (`LastTenantAdminError`); rejection of
  privilege escalation beyond the caller's own held permissions (if
  subset-delegation is confirmed); platform-tenant enforcement for system
  roles (both accept and reject paths).
- Endpoint-level tests mirroring `test_campaigns_authorization.py`'s
  pattern (unauthenticated → 401, unauthorized → 403, authorized → 200,
  cross-tenant attempt → 403/404, etc.) for each new route.

### Definition of done
- All new service methods raise the correct, specific exception from
  `app/core/exceptions.py` (already declared) for every documented failure
  mode — no bare `ValueError`s, no unhandled `IntegrityError`s reaching the
  client as a raw 500.
- All new repository methods follow the no-commit convention.
- All new endpoints are protected exclusively via
  `require_permission`/`require_system_role` dependencies — zero
  authorization logic in route bodies.
- Composite-FK-backed tenant isolation is preserved (no new query pattern
  that could bypass the existing DB-level guarantees).
- `alembic upgrade head` applies cleanly on a fresh database if any new
  migration is added; the migration is additive only.
- New tests pass; the full existing 6-test suite still passes unmodified;
  `pytest` still fails closed against a misconfigured `TEST_DATABASE_URL`.
- App import + `configure_mappers()` check still succeeds.
- No commit/push happens without explicit user instruction (per §18).

### Risks and unresolved architectural questions
- **Last-tenant-administrator definition is ambiguous** without
  re-confirming with the user — is it "holds the `tenant_admin` built-in
  role" or "holds `roles:manage` effectively" or something else? Get this
  answered before writing the check.
- **Subset-delegation enforcement** was referenced in this project's prior
  design history but is not yet reflected in any code or exception — must
  be explicitly reconfirmed as in-scope for this milestone rather than
  assumed.
- **Concurrency-safety for last-administrator protection** — a naive
  "count active admins, reject if this revoke would bring it to zero"
  check has a race condition under concurrent requests. A robust solution
  (e.g. a `SELECT ... FOR UPDATE` on the affected rows, or a
  transaction-level PostgreSQL advisory lock keyed by `tenant_id`) needs
  explicit design discussion and user sign-off before implementation — do
  not ship a naive check silently.
- **Platform-tenant bootstrap chicken-and-egg problem**: some mechanism
  must create the very first `super_admin` without already having a
  `super_admin` to authorize it. This is arguably a prerequisite for
  `SystemRoleManagementService` to be usable at all in a real environment,
  and is currently unresolved (see §15 item 3).
- **Whether `app/schemas/` (currently empty) should finally be used** for
  the new Pydantic models, vs. continuing this project's established
  pattern of colocating schemas with their router file — a small but
  real convention decision that should be made explicitly, not silently.

---

## 17. GIT STATE

- **Current branch:** `main`.
- **Latest commit:** `52fb010` — "Add protected campaigns endpoint and
  isolated authorization tests" (author `BabuBisleri313
  <taranshrthr@gmail.com>`, committed 2026-07-31 13:48:23 +0530).
- **Working tree:** clean — `git status --short` returns no output (no
  staged, unstaged, or untracked files) at the time this handoff was
  written.
- **Remote:** `origin` →
  `https://github.com/taranshrathore/verisure-ads-automation.git` (both
  fetch and push). Nothing has been pushed to it as part of this
  conversation (not verified whether `main` is ahead/behind/in-sync with
  `origin/main` — check `git status -sb` or `git fetch` before assuming
  either way).
- **Ignored local configuration required to run the project** (per
  `.gitignore`, verified): `.env` — must be created locally (not
  committed) with at minimum `DATABASE_URL`, `TEST_DATABASE_URL`,
  `JWT_SECRET_KEY` (required, no default — the app will fail to start
  without it), and the `POSTGRES_*` variables consumed by `compose.yaml`.
  `.venv/` is also gitignored (recreate via `uv sync`). The dedicated
  `verisure_test_db` PostgreSQL database itself is **not** a file and is
  not tracked by git at all — it must be created manually (`CREATE
  DATABASE verisure_test_db`) on whatever PostgreSQL server `TEST_DATABASE_URL`
  points at, exactly as documented in README.md's "Testing" section.

---

## 18. NEW-CHAT OPERATING INSTRUCTIONS

For the agent picking this up next:

1. **Inspect files before modifying them.** Do not trust this document's
   prose over the actual current file contents — re-read any file you are
   about to change first, since this handoff is a snapshot and the repo
   may have moved on.
2. **Discuss and obtain approval for architecture before implementation.**
   Especially for §16's open questions (last-admin definition,
   subset-delegation scope, concurrency strategy, schema-location
   convention) — do not silently pick an answer and build it.
3. **Implement incrementally.** Follow this project's established pattern
   (see §12): one focused capability per commit-sized change (e.g. "add
   role repository" as its own step, not bundled with "add role service"
   and "add role endpoints" all at once), mirroring how RBAC persistence,
   then the authorization engine, then the campaigns endpoint were each
   built and reviewed as separate phases.
4. **Preserve repository/service/API boundaries** exactly as described in
   §2 and §10 — repositories never commit, services own transactions and
   raise domain exceptions, routes contain no authorization or business
   logic.
5. **Run migrations and tests** after any schema or authorization-relevant
   change: `alembic upgrade head` against the dev DB, and the
   `TEST_DATABASE_URL`-pointed `alembic upgrade head` + `pytest` against
   the test DB (see §9's exact commands). Also re-run the app
   import/`configure_mappers()` sanity check.
6. **Never commit or push unless explicitly instructed.** Even after
   implementing and verifying a change, leave it uncommitted until the
   user explicitly asks for a commit (matching how every phase in this
   project's history up to and including the current `HEAD` was handled).
7. **Report files changed, commands, results, and assumptions after each
   phase** — the level of detail demonstrated in this handoff document
   (exact commands run, exact test results, exact warnings and their
   resolution, explicit call-outs of any assumption made) is the expected
   standard, not an exceptional one-off.
