# VeriSure Ad Automation — Engineering Handoff

**Purpose of this document:** a complete, self-contained handoff so that a
fresh chat/agent with zero access to prior conversation history can continue
production development safely. Everything below was verified directly
against the repository's source code, migrations, and git state at the time
this document was written. If the repository has changed since, re-verify
before trusting any specific detail — but the architectural patterns and
invariants described are meant to remain stable.

---

## 0. AUTHORIZATION STATUS — READ THIS FIRST

**There is no local RBAC in this backend today.** A complete, database-backed
RBAC implementation (roles, permissions, role assignments, system roles) was
built, then removed in its entirety, because the existing VeriSure CRM already
enforces authoritative RBAC across its own backend/APIs. Maintaining a second,
independent RBAC implementation here would be duplicate, divergence-prone
authority — two systems that could disagree about who can do what. **VeriSure
CRM is intended to become the single source of truth for roles and
permissions for this backend too.**

**What this backend has right now:**
- **Authentication only.** `get_current_user` (`app/api/dependencies.py`)
  proves the caller holds a valid, unexpired JWT access token for an active
  user of an active tenant. It grants no permissions and must never be
  treated as if it did.
- **Every endpoint in this backend enforces authentication only** — there is
  no permission/role check anywhere, on any route, including the Campaign
  Management endpoints (§8). This is an explicit, temporary state, not an
  oversight.
- `PermissionSlug` (`app/core/authorization/catalog.py`) survives, trimmed to
  `campaigns:read`/`campaigns:manage`, purely as a stable capability-*name*
  vocabulary for a future CRM-permission mapping. It performs zero
  authorization logic and is not read by any endpoint today.

**What was removed** (the local RBAC engine that used to exist, for context):
models `Permission`, `Role`, `RolePermission`, `UserRoleAssignment`,
`SystemRoleAssignment` and their tables; `AuthorizationContext`,
`AuthorizationService`, `AuthorizationRepository`,
`require_permission`/`require_system_role`, `get_authorization_context`;
`RoleManagementService`, `SystemRoleManagementService` and the `/api/v1/roles`
endpoints; the RBAC-specific exception hierarchy (`PermissionDeniedError`,
`CrossTenantAccessError`, `RoleNotFoundError`, `PermissionNotFoundError`,
`RoleAssignmentConflictError`, `ProtectedRoleError`,
`LastTenantAdminError`, `PlatformTenantRequiredError`) and their handlers;
`builtin_roles.py` and `context.py`. The schema removal is migration
`c4d8f1a9b6e3` (§5) — it drops the RBAC tables and the `uq_users_id_tenant_id`
constraint on `users` (which existed solely to back
`user_role_assignments`' composite FK), fully reversible via `downgrade()`.

**Frontend trust rule (unconditional, applies regardless of CRM integration
status):** no endpoint may ever read a role or permission value supplied
directly by the frontend/client and treat it as authoritative. Authorization
must always be derived server-side from a validated token and/or a trusted
backend-to-backend lookup.

**Information still missing from this repository that must come from the
CRM team before any real authorization check can be reintroduced** (do not
guess at any of these):
1. Who issues the user's access token: CRM, or does this backend keep
   issuing its own JWTs after some CRM identity handshake?
2. How are CRM permissions supplied: embedded token claims (exact claim
   names/format), or a callable authorization API (endpoint, request/
   response schema, latency/caching expectations)?
3. The canonical CRM permission/role vocabulary and how it maps onto this
   backend's capabilities (e.g. `campaigns:read`/`campaigns:manage`).
4. How CRM represents tenant/company identity, and how that maps onto this
   backend's `tenants` table.
5. Whether CRM user IDs match this backend's `users.id`, and if not, the
   provisioning/linking flow (JIT creation vs. a sync process).
6. Service-to-service authentication requirements for any CRM permission-
   lookup API (API key, mTLS, OAuth client-credentials?).
7. Revocation semantics: does CRM push a webhook on permission change or
   user disablement, or must this backend tolerate some staleness window?

---

## 1. PROJECT OVERVIEW

**Product name:** VeriSure Ad Automation (`app_name` default: "VeriSure Ad
Automation"; package name in `pyproject.toml`: `verisure-ad-automation`).

**Business purpose:** a multi-platform automated advertisement deployment
system — a multi-tenant B2B SaaS backend intended to let tenant
organizations manage and automate advertising campaigns across multiple ad
platforms (Meta, Google, LinkedIn, Microsoft, TikTok, Amazon, Pinterest,
Snapchat, Reddit, X — inferred from placeholder env vars in `.env.example`;
no adapter integration code exists yet beyond one abstract base class stub).

**Current backend scope (what actually exists today):**
- Multi-tenant authentication (login, refresh-token rotation, logout,
  logout-all) — fully implemented and tested.
- **No local RBAC** — see §0.
- **Campaign Management, Milestone 1**: a real `Campaign` model, migration,
  repository, service, and a full CRUD + archive API — see §7. This replaces
  what used to be a stub endpoint returning an empty list.
- Numerous empty placeholder packages (`app/adapters`, `app/agents`,
  `app/config`, `app/deterministic`, `app/middleware`, `app/orchestration`,
  `app/registry`, `app/schemas`, `app/utils`) exist as scaffolding from
  initial project setup. Most contain only an empty `__init__.py`.
  `app/adapters/base_adapter.py` contains one abstract class stub
  (`BaseAdapter(ABC)`) with no methods defined yet. **Treat these as
  unimplemented scaffolding, not as evidence of hidden functionality.**

**Technology stack (verified from `pyproject.toml` and imports):**
- Python ≥3.12 (`requires-python = ">=3.12"`).
- **FastAPI** ≥0.141.1 — web framework.
- **SQLAlchemy** ≥2.0.51 — ORM, synchronous (no async engine anywhere).
- **Alembic** ≥1.18.5 — schema migrations.
- **psycopg[binary]** ≥3.3.4 — PostgreSQL driver (`postgresql+psycopg://` URLs).
- **PostgreSQL 17** (via `compose.yaml`, official `postgres:17` image).
- **pydantic-settings** ≥2.14.2 — environment-backed settings.
- **pyjwt** ≥2.13.0 — JWT access-token signing/verification.
- **pwdlib[argon2]** ≥0.3.0 — Argon2id password hashing.
- **uvicorn** ≥0.52.0 — ASGI server.
- Dev-only: **pytest** ≥9.1.1, **httpx** ≥0.28.1 and **httpx2** ≥2.9.1 (both
  present; `httpx2` avoids a `StarletteDeprecationWarning` from
  `fastapi.testclient.TestClient`).
- Dependency management: **uv** (`uv.lock` present; `[tool.uv]
  dev-dependencies` uses uv's now-deprecated `dev-dependencies` key rather
  than `[dependency-groups] dev` — cosmetic, not yet migrated).

**Development environment and commands:**
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
This environment uses the repo's own `.venv` directly:
`.venv\Scripts\python.exe -m alembic|pytest` (Windows/PowerShell); `uv run
<cmd>` is the cross-platform equivalent.

---

## 2. CURRENT REPOSITORY STRUCTURE

```
Verisure-ads-automation/
├── alembic/
│   ├── env.py                          # reads settings.database_url only
│   └── versions/
│       ├── e1c130d4a242_create_tenant_and_user_tables.py
│       ├── eafc0d83cadb_add_refresh_token_table.py
│       ├── fd90462691b5_add_rbac_tables.py           # historical, undone below
│       ├── b7c3e5a9d214_seed_rbac_catalog.py         # historical, undone below
│       ├── c4d8f1a9b6e3_remove_local_rbac_tables.py  # drops the RBAC schema
│       └── 836f99e46ed7_add_campaigns_table.py       # current head
├── alembic.ini
├── compose.yaml                        # postgres:17 service only
├── .env                                 # gitignored, local secrets/config
├── .env.example                         # committed template (out of sync
│                                         #  with actual Settings fields —
│                                         #  see §11)
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
    │       ├── __init__.py              # re-exports PermissionSlug/PERMISSION_DESCRIPTIONS
    │       └── catalog.py               # PermissionSlug StrEnum -- vocabulary only, see §0
    ├── database/
    │   ├── base.py                      # DeclarativeBase `Base`
    │   └── session.py                   # get_engine()/SessionFactory() (DATABASE_URL only)
    ├── models/
    │   ├── __init__.py                  # imports all models onto Base.metadata
    │   ├── mixins.py                    # UUIDPrimaryKeyMixin/TimestampMixin/SoftDeleteMixin
    │   ├── tenant.py
    │   ├── user.py
    │   ├── refresh_token.py
    │   └── campaign.py
    ├── repositories/
    │   ├── tenant_repository.py
    │   ├── user_repository.py
    │   ├── refresh_token_repository.py
    │   └── campaign_repository.py
    ├── services/
    │   ├── auth_service.py               # AuthService (owns commits)
    │   └── campaign_service.py           # CampaignService (owns commits)
    ├── api/
    │   ├── dependencies.py               # get_db, get_current_user, repo/service providers
    │   └── v1/
    │       ├── auth.py                   # /api/v1/auth/* router
    │       └── campaigns.py              # /api/v1/campaigns/* router
    └── tests/
        ├── __init__.py
        ├── database.py                   # dedicated TEST_DATABASE_URL engine + validation
        ├── conftest.py                   # pytest_configure guard + db_session/client/auth_fixture
        ├── test_auth.py                  # 6 tests
        ├── test_rbac_removed.py          # 2 tests
        ├── test_campaign_model.py        # 7 tests
        ├── test_campaign_service.py      # 21 tests
        ├── test_campaigns.py             # 28 tests
        └── test_campaign_migration.py    # 2 tests
```

**Empty/unused scaffolding packages:** `app/adapters/` (one abstract stub,
`BaseAdapter`), `app/agents/`, `app/config/`, `app/deterministic/`,
`app/middleware/`, `app/orchestration/`, `app/registry/`, `app/schemas/`,
`app/utils/`. Placeholders only — do not assume hidden logic lives in them.

**Ownership of each architectural layer:**
- `app/models/` — SQLAlchemy ORM table definitions only; no business logic.
- `app/repositories/` — data access only. **Never commit or roll back**
  (explicit convention, stated in every repository's docstring). Return ORM
  objects or scalar values; never raise domain exceptions.
- `app/services/` — orchestrate one or more repositories inside a single
  request's use case; **own all `session.commit()`/`rollback()` calls**;
  raise domain exceptions from `app/core/exceptions.py`.
- `app/api/` — FastAPI routers, Pydantic request/response models, and
  dependency wiring only. Routes call a service method and translate the
  result to a response model.
- `app/core/` — framework-agnostic building blocks (settings, security
  primitives, the exception hierarchy, the authorization vocabulary seam).

---

## 3. APPLICATION ARCHITECTURE

**FastAPI application composition** (`app/main.py`):
1. `configure_logging()` is called once at import time.
2. `FastAPI(...)` app is constructed with `title=settings.app_name`,
   `docs_url="/docs"`, `redoc_url="/redoc"`.
3. `register_exception_handlers(app)` wires the custom exception → HTTP
   mapping (§4).
4. `CORSMiddleware` is added with `allow_origins=["*"]` — explicitly marked
   with a `# TODO: Restrict these CORS settings before deploying to
   production.` comment. Known, intentional gap.
5. An `api_v1_router = APIRouter(prefix="/api/v1")` aggregates `auth.router`
   and `campaigns.router`, then is included on `app`.
6. Two unauthenticated utility endpoints: `GET /` (service identification)
   and `GET /health` (liveness).

**Settings** (`app/core/settings.py`): `Settings(BaseSettings)`,
`model_config = SettingsConfigDict(env_file=".env", extra="ignore")`.
Fields: `app_name`, `app_env`, `debug`, `api_version`, `database_url: str |
None`, `test_database_url: str | None` (test-only, see §9),
`jwt_secret_key: str` (required, no default), `jwt_algorithm`,
`jwt_access_token_expire_minutes` (default 15), `jwt_issuer`, `jwt_audience`.
A module-level singleton `settings = get_settings()` is created at import
time via an `lru_cache`d `get_settings()`. The database URL is **not**
validated at settings-import time; validation is deferred to
`app/database/session.py`'s `get_engine()`.

**Database engine and session lifecycle** (`app/database/session.py`):
- `get_engine()` — `lru_cache`d, lazily calls `create_engine(
  settings.database_url)`; raises `RuntimeError` if `DATABASE_URL` is unset,
  only when first called.
- `SessionFactory()` — `lru_cache`d `sessionmaker(bind=get_engine(),
  autocommit=False, autoflush=False, class_=Session)`.
- Request-scoped session: `app/api/dependencies.py::get_db()` is a generator
  dependency (`session = SessionFactory()(); yield session; finally:
  session.close()`). It never commits — every commit is owned by a service.
- No async engine/session exists anywhere (explicit `# TODO`).

**SQLAlchemy conventions:**
- SQLAlchemy 2.x typed declarative style throughout: `Mapped[...]`,
  `mapped_column(...)`, `DeclarativeBase` (`app/database/base.py`).
- UUID primary keys everywhere via `UUIDPrimaryKeyMixin`
  (`sqlalchemy.dialects.postgresql.UUID(as_uuid=True)`, Python-side
  `default=uuid.uuid4`).
- `TimestampMixin` — `created_at`/`updated_at`, both `server_default=
  func.now()`; `updated_at` also has `onupdate=func.now()` (applies to both
  ORM-flush-driven updates and the Core-style conditional `UPDATE`
  statements used by `CampaignRepository` — verified, not just assumed).
- `SoftDeleteMixin` — nullable `deleted_at`; **no query-level filtering or
  delete behavior is implemented anywhere** — every place that cares about
  soft-deletion filters `deleted_at.is_(None)` explicitly (e.g.
  `CampaignRepository`, `AuthService.login`). No code path sets `deleted_at`
  on a `Campaign` today — archiving is a status transition, not a deletion.
- Every foreign key and constraint in every model has an explicit,
  human-readable `name=` — no unnamed constraints exist. New constraints
  must follow this.
- `relationship(..., lazy="selectin")` is used where relationships exist
  (`Tenant.users`, `User.tenant`, `RefreshToken.user`). `Campaign`
  deliberately declares **no** ORM relationships to `Tenant`/`User`: its
  `tenant_id` participates in two separate FK constraints (a plain FK, and
  the composite creator FK in `__table_args__`), which would make a
  `relationship()` join condition ambiguous without extra `foreign_keys=`/
  `overlaps=` wiring not needed yet. Callers use the plain UUID columns
  directly.

**Repository/service/API boundaries:** see §2 "Ownership" — this is the
load-bearing convention of the codebase, enforced by explicit docstrings in
nearly every file.

**Dependency-injection flow** (`app/api/dependencies.py`):
```
get_db()
  → get_tenant_repository(db) / get_user_repository(db) /
    get_refresh_token_repository(db) / get_campaign_repository(db)
      → get_auth_service(...) / get_campaign_service(...)

oauth2_scheme (Bearer) → get_current_user(token, tenant_repository, user_repository)
  → User (authentication only -- see §0; no authorization dependency exists)
```
FastAPI's per-request dependency cache guarantees `get_db` and
`get_current_user` each execute **at most once per request**.

**Logging** (`app/core/logging.py`): `configure_logging()` calls
`logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s |
%(levelname)s | %(message)s")`; a single named logger `logger =
logging.getLogger("verisure")` is exported.

---

## 4. EXCEPTION ARCHITECTURE

`app/core/exceptions.py` + `app/core/exception_handlers.py`:

A framework-agnostic hierarchy rooted at `AppError(Exception)`, with a
default message set in `__init__`.

| Exception | Parent | HTTP status |
|---|---|---|
| `AuthenticationError` | `AppError` | 401 (+`WWW-Authenticate: Bearer`) — registered once, covers the whole subtree via FastAPI's MRO-based dispatch |
| `InvalidCredentialsError` | `AuthenticationError` | 401 |
| `InvalidAccessTokenError` | `AuthenticationError` | 401 |
| `InvalidRefreshTokenError` | `AuthenticationError` | 401 |
| `RefreshTokenExpiredError` | `AuthenticationError` | 401 |
| `RefreshTokenRevokedError` | `AuthenticationError` | 401 |
| `RefreshTokenReuseError` | `AuthenticationError` | 401 |
| `TenantNotFoundError` | `AppError` | 404 |
| `TenantInactiveError` | `AppError` | 403 |
| `UserNotFoundError` | `AppError` | 404 |
| `UserInactiveError` | `AppError` | 403 |
| `CampaignNotFoundError` | `AppError` | 404 |
| `InvalidCampaignStateError` | `AppError` | 409 |
| `CampaignValidationError` | `AppError` | 422 |

All responses are `{"detail": <message>}`. `CampaignNotFoundError` is
deliberately used for both a genuinely missing campaign and a cross-tenant
lookup (a campaign that exists but belongs to a different tenant) — the two
cases are indistinguishable by design, to avoid leaking cross-tenant
existence.

The local RBAC exception hierarchy (`PermissionDeniedError`,
`CrossTenantAccessError`, `RoleNotFoundError`, `PermissionNotFoundError`,
`RoleAssignmentConflictError`, `ProtectedRoleError`, `LastTenantAdminError`,
`PlatformTenantRequiredError`) and their handlers were removed along with
the local RBAC engine (§0) and do not exist anywhere in this codebase today.

---

## 5. DATABASE AND MIGRATIONS

**Current tables** (4, all verified in models + migrations):

| Table | Key columns | Notes |
|---|---|---|
| `tenants` | `id` PK, `name`, `slug` unique, timestamps, `deleted_at` | Top-level tenancy boundary |
| `users` | `id` PK, `tenant_id` FK→tenants, `email`, `hashed_password`, `role` (flat string, unused — see §11), timestamps, `deleted_at` | `UNIQUE(tenant_id, email)`; `UNIQUE(id, tenant_id)` exists to be the referenced side of `campaigns`' composite FK (below) |
| `refresh_tokens` | `id` PK, `user_id` FK→users, `family_id`, `token_hash` unique, `replaced_by_token_id` FK→self, `revoked_at`, `expires_at`, timestamps | No raw token ever stored — only SHA-256 hash |
| `campaigns` | `id` PK, `tenant_id` FK→tenants, `created_by_user_id`, `name`, `objective`, `budget_type`, `budget_amount`, `currency`, `start_at`, `end_at`, `status`, timestamps, `deleted_at` | See §7 for full detail |

**Important constraints and indexes (all named, verified):**
- `uq_users_tenant_id_email` — one email per tenant.
- `uq_users_id_tenant_id` — the referenced side of `campaigns`'
  `fk_campaigns_created_by_user_id_tenant_id_users` composite FK
  `(created_by_user_id, tenant_id) → users(id, tenant_id)`. This makes it
  **structurally impossible** for a campaign to claim a creator who belongs
  to a different tenant — not just application-logic-prevented. (This same
  constraint previously backed `user_role_assignments`' composite FK before
  RBAC removal; it was dropped by `c4d8f1a9b6e3` and re-added by
  `836f99e46ed7` once `campaigns` became its new consumer.)
- `fk_campaigns_tenant_id_tenants` — plain FK, `campaigns.tenant_id → tenants.id`.
- `ck_campaigns_budget_fields_all_or_none` — `budget_type`, `budget_amount`,
  and `currency` must be all NULL or all NOT NULL together.
- `ck_campaigns_budget_amount_positive` — `budget_amount IS NULL OR
  budget_amount > 0`.
- `ck_campaigns_currency_iso4217` — `currency IS NULL OR currency ~
  '^[A-Z]{3}$'`.
- `ck_campaigns_schedule_order` — `start_at IS NULL OR end_at IS NULL OR
  end_at > start_at`.
- `ix_campaigns_tenant_id_status_active` — partial index on `(tenant_id,
  status) WHERE deleted_at IS NULL`, supporting `CampaignRepository`'s
  tenant-scoped list/filter queries.

**Tenant-isolation guarantees:**
1. Every tenant-scoped table carries an explicit `tenant_id` FK.
2. `campaigns`' composite creator FK (above) makes cross-tenant campaign
   creatorship structurally impossible at the database level.
3. Every repository method that reads or writes a tenant-scoped row takes
   `tenant_id` as an explicit parameter and includes it in the `WHERE`
   predicate — there is no method on any repository that can look up or
   mutate a row without a tenant filter.

**Soft-deletion behaviour:**
- `tenants`, `users`, `campaigns` have `deleted_at` (via `SoftDeleteMixin`).
- **No automatic query filtering exists.** Every read path that needs to
  respect soft-deletion filters `deleted_at.is_(None)` explicitly. If you add
  a new read path over a soft-deletable table, you must add this filter
  yourself.
- No code path sets `deleted_at` on a `Campaign` today. Archiving
  (`CampaignService.archive_campaign`) sets `status = 'archived'` and never
  touches `deleted_at` — an archived campaign remains fully retrievable and
  appears in default list results, by design (archiving is a terminal
  business state, not deletion).

**Alembic migration history, in exact order** (verified via `alembic
heads`/`current` and file contents):
1. `e1c130d4a242` — *create tenant and user tables* (`down_revision=None`,
   the root). Creates `tenants`, `users`.
2. `eafc0d83cadb` — *add refresh token table*. Creates `refresh_tokens`.
3. `fd90462691b5` — *add rbac tables*. Created the local RBAC schema
   (`permissions`, `roles`, `role_permissions`, `user_role_assignments`,
   `system_role_assignments`) and `uq_users_id_tenant_id` on `users`.
4. `b7c3e5a9d214` — *seed rbac catalog*. A pure data migration that seeded
   the RBAC tables created by `fd90462691b5`.
5. `c4d8f1a9b6e3` — *remove local rbac tables*. Drops every table created by
   `fd90462691b5` (in reverse dependency order) and `uq_users_id_tenant_id`.
   `downgrade()` fully recreates the schema and re-seeds the catalog.
6. `836f99e46ed7` — *add campaigns table* (**current head**). Creates the
   three PostgreSQL enum types (`campaign_objective`, `campaign_budget_type`,
   `campaign_status`), re-adds `uq_users_id_tenant_id` on `users` (now backing
   `campaigns`' composite FK instead), and creates `campaigns` with all its
   FKs, CHECK constraints, and the partial index described in §5/§7.

**Important note on migrations 3–4:** `fd90462691b5` and `b7c3e5a9d214` are
frozen, already-applied history — **never edit them**. Running `alembic
upgrade head` on a brand-new database will create the full RBAC schema and
seed data at steps 3–4, then immediately drop it at step 5. This is expected
and correct: it is not a bug, and it must not be "optimized away" by editing
history. Any future schema change ships as a new, additive migration with
`down_revision` pointing at the current head.

---

## 6. AUTHENTICATION

**Password hashing** (`app/core/security/password.py`): Argon2id via
`pwdlib.PasswordHash.recommended()`. `hash_password(password) -> str`,
`verify_password(password, password_hash) -> bool`.

**Access-token implementation** (`app/core/security/jwt.py`):
- `create_access_token(user_id, tenant_id) -> str` — signs a JWT with claims
  `sub` (user id), `tenant_id`, `type="access"`, `jti` (random UUID),
  `iat`/`nbf`/`exp` (from `settings.jwt_access_token_expire_minutes`, default
  15 min), `iss`/`aud` from settings. Algorithm from `settings.jwt_algorithm`
  (default `HS256`), secret from `settings.jwt_secret_key`.
- `decode_access_token(token) -> dict` — validates signature, issuer,
  audience via PyJWT; additionally checks `claims["type"] == "access"`.
- Stateless: the JWT itself is never persisted. Every request still hits the
  database to re-verify the tenant and user are still active (see
  `get_current_user` below) — deliberately, to prevent a still-valid,
  unexpired JWT from granting access after deletion/deactivation (a
  documented `# TODO` acknowledges the extra query cost and defers caching).

**Refresh-token implementation** (`app/core/security/tokens.py` +
`app/models/refresh_token.py` + `app/repositories/refresh_token_repository.py`
+ `app/services/auth_service.py`):
- Opaque, not a JWT: `generate_refresh_token() -> secrets.token_urlsafe(32)`.
- Only `hash_token(raw) -> hashlib.sha256(raw).hexdigest()` is persisted, in
  `refresh_tokens.token_hash` (unique). The raw token is returned to the
  client once and never stored server-side.
- **Refresh rotation:** `AuthService.refresh(raw_refresh_token)` looks up the
  token by hash; if valid, creates a brand-new `RefreshToken` row (new id,
  same `family_id`), sets `replaced_by_token_id` on the old row, and revokes
  the old row — atomically inside one `session.commit()`.
- **Reuse detection:** if the looked-up token already has
  `replaced_by_token_id IS NOT NULL`, the entire token family is revoked and
  `RefreshTokenReuseError` is raised.
- **Logout/revocation:** `AuthService.logout` revokes exactly one token by
  hash; `AuthService.logout_all(tenant_id, user_id)` revokes every active
  token for that user.
- **Known, documented TOCTOU race** in `refresh()`: concurrent refresh
  requests using the same token could both read it as valid before either
  commits. An explicit `# TODO` proposes a `SELECT ... FOR UPDATE` row lock
  or an atomic conditional `UPDATE ... WHERE ...` with a row-count check.
  **This is known technical debt, not a design decision to preserve** — see
  §11.

**`get_current_user`** (`app/api/dependencies.py`): decodes the JWT,
validates `claims["type"] == "access"`, extracts `tenant_id`/`sub` (user id),
looks up the tenant (must exist, `deleted_at IS NULL`) then the user (must
exist, `deleted_at IS NULL`, and must actually belong to that `tenant_id` —
the lookup is `UserRepository.get_by_id(tenant_id, user_id)`, tenant-scoped).
Any failure raises a local `_unauthorized()` helper directly (a narrow
`except` tuple, not a blanket `except Exception`) → 401 with
`WWW-Authenticate: Bearer`.

**Auth endpoints** (`app/api/v1/auth.py`, all under `/api/v1/auth`):
- `POST /login` — `{tenant_slug, email, password}` → `200
  {access_token, refresh_token}`.
- `POST /refresh` — `{refresh_token}` → `200 {access_token, refresh_token}`.
- `POST /logout` — `{refresh_token}` → `204`.
- `POST /logout-all` — requires a valid access token → `204`.

**Security invariants (verified as currently implemented):**
- Uniform `InvalidCredentialsError` on **any** login failure (bad tenant, bad
  email, inactive tenant/user, bad password) — every failure path raises the
  same exception type with the same default message, to prevent
  tenant/user-existence enumeration.
- No raw refresh token is ever persisted (only its SHA-256 hash).
- `get_current_user` re-validates tenant/user existence and activity on
  every request (no trust in JWT claims beyond signature/expiry).

---

## 7. CAMPAIGN MANAGEMENT (MILESTONE 1)

**Scope:** draft creation, retrieval, listing, partial editing, and
draft-to-archived transition only. There is no "ready"/publish transition
and no ad-provider (Meta/Google) integration yet.

**Model** (`app/models/campaign.py`):
- `Campaign(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base)`,
  table `campaigns`.
- Required: `tenant_id`, `created_by_user_id`, `name`, `status` (server
  default `'draft'`).
- Nullable (a draft may be genuinely incomplete): `objective`,
  `budget_type`, `budget_amount` (`Numeric(12, 2)`), `currency` (`CHAR(3)`),
  `start_at`/`end_at` (`DateTime(timezone=True)`).
- Three PostgreSQL enums, all persisted by lowercase string value (not
  Python member name, via `values_callable`):
  - `CampaignObjective`: `awareness`, `traffic`, `leads`, `conversions`.
  - `CampaignBudgetType`: `daily`, `lifetime`.
  - `CampaignStatus`: `draft`, `ready`, `publishing`, `active`, `paused`,
    `completed`, `failed`, `archived`. **Only `draft` and `archived` are
    reachable through `CampaignService`/the API today.** The remaining
    members are declared now so that adding them later never requires an
    `ALTER TYPE ... ADD VALUE` migration (awkward, and transaction-unsafe on
    older PostgreSQL) — but nothing in this codebase can set a campaign to
    one of them yet.
- Tenant ownership is enforced structurally, not just by convention: see the
  `uq_users_id_tenant_id` / composite FK description in §5.
- No `relationship()` to `Tenant`/`User` — see §3's SQLAlchemy-conventions
  note on why.

**Repository** (`app/repositories/campaign_repository.py`) — never commits;
every method is tenant-scoped:
- `create(campaign)` — stages an INSERT.
- `get_by_tenant_and_id(tenant_id, campaign_id)` — tenant- and
  soft-delete-scoped single lookup.
- `list_by_tenant(tenant_id, *, limit, offset, status=None)` — paginated,
  optionally status-filtered. Ordered by `created_at DESC, id DESC`: `id` is
  a deliberate tie-breaker, since `created_at` alone is not a unique sort key
  (two campaigns created in the same transaction can share an identical
  value — PostgreSQL's `now()` is transaction-start time, not statement
  time) and `LIMIT`/`OFFSET` over a non-unique sort key produces
  non-deterministic paging.
- `update_draft_fields(tenant_id, campaign_id, values)` /
  `archive_draft(tenant_id, campaign_id)` — both issue a single conditional
  `UPDATE ... WHERE status = 'draft'` and return the affected row count.
  Re-checking `status = 'draft'` in the `WHERE` clause (not just at an
  earlier read) closes the race where a concurrent archive/edit lands
  between a caller's read and its write; a `0` row count tells the caller it
  lost that race without a second query.

**Service** (`app/services/campaign_service.py`) — owns all commits;
`_validate_campaign_fields` is the single source of truth for domain
validation, mirroring every database CHECK constraint in Python so invalid
input fails as a clean `422 CampaignValidationError` instead of a raw
`IntegrityError` surfacing as a 500:
- Budget triple (`budget_type`/`budget_amount`/`currency`) must be all-or-none.
- `budget_amount` must be positive, have at most two decimal places, and not
  exceed `9999999999.99` (PostgreSQL's `NUMERIC(12, 2)` would otherwise
  silently round or overflow rather than error).
- `currency` must be exactly three uppercase ASCII letters.
- `start_at`/`end_at`, if present, must be timezone-aware (a naive datetime
  compared against an aware one raises `TypeError` in Python, not a domain
  error) and `end_at` must be strictly after `start_at`.
- `create_draft` — only `name` (plus tenant/creator/status) is required.
- `get_campaign` / `list_campaigns` — tenant-scoped reads;
  `CampaignNotFoundError` on a missing or cross-tenant id.
- `update_draft(tenant_id, campaign_id, updates)` — `updates` must contain
  only explicitly-set keys (built from
  `payload.model_dump(exclude_unset=True)` at the API layer), so an omitted
  field is left untouched while an explicit `null` clears it. The full
  merged result (existing values overridden only by explicitly-provided
  keys) is validated as a whole before the single conditional `UPDATE` is
  attempted. A `0` affected-row count raises `InvalidCampaignStateError`
  (409) — the campaign was not a draft at write time.
- `archive_campaign` — same conditional-write pattern; `InvalidCampaignStateError`
  (409) if the campaign was not a draft.

**API** (`app/api/v1/campaigns.py`, prefix `/campaigns`) — every route
requires `Depends(get_current_user)` (authentication only, see §0); `tenant_id`
and `created_by_user_id` are always derived from the authenticated user, never
from the request body. All Pydantic models use `ConfigDict(extra="forbid")`.

| Method & path | Request model | Notes |
|---|---|---|
| `POST /campaigns` | `CampaignCreateRequest` | Only `name` required |
| `GET /campaigns` | — (`limit`/`offset`/`status` query params) | `limit` 1–200 (default 50), `offset` ≥0 |
| `GET /campaigns/{campaign_id}` | — | Cross-tenant → 404 |
| `PATCH /campaigns/{campaign_id}` | `CampaignUpdateRequest` (all fields optional) | Draft-only; non-draft → 409 |
| `POST /campaigns/{campaign_id}/archive` | — | Draft-only; non-draft → 409 |

All five return `CampaignRead` (or `CampaignListResponse{items: [CampaignRead]}`).

---

## 8. IMPLEMENTED API ENDPOINTS (COMPLETE, VERIFIED)

| Method & Path | Auth required | Authorization required | Status |
|---|---|---|---|
| `GET /` | None | None | Trivial identification payload |
| `GET /health` | None | None | Liveness only |
| `POST /api/v1/auth/login` | None (credential exchange) | None | Implemented |
| `POST /api/v1/auth/refresh` | None (refresh token is the credential) | None | Implemented (rotation + reuse detection) |
| `POST /api/v1/auth/logout` | None (refresh token is the credential) | None | Implemented |
| `POST /api/v1/auth/logout-all` | Bearer JWT | None | Implemented |
| `POST /api/v1/campaigns` | Bearer JWT | **None — see §0** | Implemented (draft create) |
| `GET /api/v1/campaigns` | Bearer JWT | **None — see §0** | Implemented (paginated, status filter) |
| `GET /api/v1/campaigns/{campaign_id}` | Bearer JWT | **None — see §0** | Implemented |
| `PATCH /api/v1/campaigns/{campaign_id}` | Bearer JWT | **None — see §0** | Implemented (draft-only) |
| `POST /api/v1/campaigns/{campaign_id}/archive` | Bearer JWT | **None — see §0** | Implemented (draft-only) |

`/api/v1/roles` and every other RBAC-management route **do not exist** —
routing to them returns `404`, verified by `test_rbac_removed.py`.

---

## 9. TESTING ARCHITECTURE

**pytest configuration:** `[tool.pytest.ini_options] testpaths =
["app/tests"]` in `pyproject.toml`.

**Current suite: 66 tests total, across 6 files** (verified via `pytest
--collect-only -q`):

| File | Tests | Covers |
|---|---|---|
| `test_auth.py` | 6 | Login, wrong password, unauthenticated, soft-deleted user/tenant rejection, cross-tenant token forgery rejection |
| `test_rbac_removed.py` | 2 | `/api/v1/roles` returns 404; no dropped RBAC table is required by app startup |
| `test_campaign_model.py` | 7 | Database-level CHECK/FK constraints, exercised directly via the ORM |
| `test_campaign_service.py` | 21 | `CampaignService` business logic: CRUD, archiving, tenant isolation, race-guard behavior, validation edge cases |
| `test_campaigns.py` | 28 | `/api/v1/campaigns` HTTP-level behavior: auth, happy paths, tenant isolation, validation, status conflicts, pagination |
| `test_campaign_migration.py` | 2 | Alembic downgrade/upgrade round-trip; zero autogenerate drift after it |

**`TEST_DATABASE_URL`:** a dedicated `Settings.test_database_url: str |
None` field, read only by `app/tests/database.py` — the application itself
never reads it. Configured locally in `.env` (gitignored).

**Dedicated test engine/session** (`app/tests/database.py`): builds its
`Engine` exclusively from `resolve_test_database_url()`; **never imports
`app.database.session`** (the application's own `DATABASE_URL`-backed
engine), by design.

**Fail-closed safeguards** — `resolve_test_database_url()` raises
`TestDatabaseConfigurationError` (caught by `pytest_configure` in
`conftest.py`, re-raised as `pytest.UsageError`, aborting before any test
runs) if, in order: (1) `TEST_DATABASE_URL` is unset — no fallback to
`DATABASE_URL`; (2) it equals `DATABASE_URL` exactly; (3) its database name
does not contain the substring `"test"` (case-insensitive).

**Savepoint/transaction rollback strategy** (`app/tests/conftest.py`): each
test gets one `engine.connect()` → `connection.begin()` (outer transaction)
→ `Session(bind=connection, join_transaction_mode="create_savepoint")`.
Application code can freely call `session.commit()` exactly as in
production — each commit only releases a SAVEPOINT. Teardown always does
`session.close(); transaction.rollback(); connection.close()`
unconditionally, so no row survives a test whether it passed, failed, or
was interrupted.

**FastAPI dependency override:** the `client` fixture overrides **only**
`app.api.dependencies.get_db`, to yield the exact same `db_session` used by
test setup code. `get_current_user` is never mocked or overridden — tests
exercise the real JWT → session → repository → endpoint chain.

**`auth_fixture`** (`conftest.py`): a factory fixture (`make(...)`) that
creates a tenant + user directly in the test database and returns `(user,
bearer_token)`. It carries no role or permission data — see §0.

**Preparing the test database:** run migrations against
`TEST_DATABASE_URL` by temporarily overriding `DATABASE_URL` for one
`alembic upgrade head` invocation (documented, exact commands in README.md's
"Testing" section). `Base.metadata.create_all()` is never used as a
substitute for migrations, including for tests.

**Alembic migration tests specifically** (`test_campaign_migration.py`) run
`alembic` as a subprocess with `DATABASE_URL` overridden in the subprocess's
own environment only — `app.core.settings.settings` is a module-level
singleton already imported by the test process, so mutating `os.environ`
in-process would not reliably affect `alembic/env.py`'s already-bound
reference to it. A subprocess reads the environment fresh at startup,
sidestepping that entirely.

---

## 10. PRODUCTION INVARIANTS

Rules that any future implementation **must not violate**:

1. **No repository bypass from API routes.** Routes call services; services
   call repositories. No router file imports a repository class directly.
2. **Repositories never call `session.commit()`/`rollback()`** — only
   services do.
3. **No raw refresh-token storage.** Only `hash_token(raw)` (SHA-256) is
   ever written to `refresh_tokens.token_hash`.
4. **No silent test fallback to development DB.**
   `resolve_test_database_url()` raises immediately; there is no code path
   where a missing/invalid `TEST_DATABASE_URL` results in tests quietly
   running against `DATABASE_URL`.
5. **No `Base.metadata.create_all()` in migration-backed integration
   tests.** The test database schema comes exclusively from `alembic
   upgrade head`.
6. **Every tenant-scoped repository query takes `tenant_id` as an explicit
   parameter and filters on it.** A cross-tenant lookup must raise/return
   "not found," never leak the row.
7. **Every new FK/unique/check constraint gets an explicit, descriptive
   `name=`.**
8. **Migrations already applied must not be rewritten.** `e1c130d4a242`,
   `eafc0d83cadb`, `fd90462691b5`, `b7c3e5a9d214`, and `c4d8f1a9b6e3` are
   frozen history. Any schema change ships as a **new, additive** migration.
9. **No endpoint may read a role or permission value supplied directly by
   the frontend/client and treat it as authoritative** (see §0) — this rule
   applies unconditionally, regardless of CRM integration status.
10. **Conditional state-transition writes use a single `UPDATE ... WHERE
    <expected-state predicate>` with a row-count check**, not a
    read-then-write pair, whenever a lost-update race is possible (see
    `CampaignRepository.update_draft_fields`/`archive_draft`).

---

## 11. KNOWN TECHNICAL DEBT AND LIMITATIONS

Only issues genuinely present and verified in the repository:

1. **Refresh-token rotation TOCTOU race** (`auth_service.py::refresh`) —
   documented in code, not yet fixed. Two concurrent refresh calls with the
   same token can both pass validation before either commits.
2. **`.env.example` is out of sync with `Settings`** — it lists `SECRET_KEY`,
   `ENCRYPTION_KEY`, and dozens of ad-platform credential placeholders (Meta,
   Google, LinkedIn, etc.) that `Settings` does not define/read at all, while
   omitting `JWT_SECRET_KEY` and its siblings, which the app actually
   requires. Treat `.env.example` as a legacy/aspirational template.
3. **`uv`'s `dev-dependencies` key is deprecated** — `pyproject.toml` still
   uses `[tool.uv] dev-dependencies = [...]`; cosmetic today.
4. **No connection pool tuning** — default SQLAlchemy pool settings only.
5. **Flat `role: str` column still exists on `User`** (`default="member"`).
   It is not read by any authorization logic (there is none — see §0). Dead
   weight, not a competing authorization source; removal/repurposing is a
   decision for CRM integration work, not scheduled.
6. **No rate limiting, no audit logging, no request-id/correlation-id
   middleware** — none of these exist yet.
7. **CORS is wide open** (`allow_origins=["*"]`) — explicit pre-production
   `# TODO` in `main.py`.
8. **`TEST_DATABASE_URL` safety check is host-agnostic** — a simple string
   check on the database name in the URL, not the host; pointing at a
   same-named database on an unintended host would still pass. Accepted
   trade-off, not a full network-topology safeguard.
9. **No local RBAC / CRM authorization contract pending** — see §0 for the
   exact list of information needed from the CRM team before any real
   permission check can be reintroduced.
10. **Campaign Management is Milestone 1 only** — no readiness/publish
    transition, no audience/creative/targeting data model, and no Meta/Google
    adapter integration exist yet (see §12).

---

## 12. NEXT RECOMMENDED MILESTONE

Two independent tracks are ready to proceed; neither blocks the other:

**A. CRM authorization integration** — blocked on external input, not on
this codebase. Cannot proceed until the CRM team answers the seven open
questions in §0. Once answered, the recommended shape is: a new
CRM-backed dependency (e.g. `get_authorization_context`) that endpoints can
depend on instead of, or in addition to, `get_current_user`; `PermissionSlug`
already exists as the target capability-name vocabulary to map onto.

**B. Campaign Management, Milestone 2** — a natural, self-contained next
step: introduce whatever data model campaign "readiness" actually requires
(audience/targeting, creative reference, destination) before implementing
the `draft → ready` transition, then design the Meta/Google adapter seam
(a provider-neutral campaign specification in, provider IDs/status/errors
out) and a `CampaignDeployment`-style child record so one campaign can target
multiple providers without duplicating the whole campaign row. Should be
designed and approved before implementation, following the same
incremental, one-capability-per-change pattern used for Milestone 1.

Do not implement either track without first inspecting the current
repository state again (this document is a snapshot) and getting explicit
approval for the design.

---

## 13. CODING CONVENTIONS

- **Naming:** `snake_case` for functions/variables/modules, `PascalCase` for
  classes, `SCREAMING_SNAKE_CASE` for module-level constants. Private/
  internal names get a leading underscore (`_session`, `_json_error`,
  `_validate_campaign_fields`).
- **Type hints:** exhaustive, modern-Python style — `str | None`, `list[str]`,
  `dict[str, object]`. Every function signature is fully annotated.
- **SQLAlchemy 2.x style:** `Mapped[...]` + `mapped_column(...)` for every
  column; `select(...)`/`update(...)` + `session.scalar(...)`/
  `session.scalars(...)`/`session.execute(...)` for queries (not the legacy
  `Query` object).
- **Pydantic conventions:** every request/response model sets `model_config
  = ConfigDict(extra="forbid")`. `model_dump(exclude_unset=True)` is the
  established pattern for PATCH semantics (see `CampaignUpdateRequest`).
- **Exception handling:** raise from the `app/core/exceptions.py` hierarchy
  in services; never raise a bare `ValueError`/`Exception` for domain
  errors. Narrow `except (SpecificError1, SpecificError2) as exc:` tuples,
  never bare `except Exception` in security-relevant code.
- **Domain validation mirrors database constraints in the service layer**
  (e.g. `CampaignService._validate_campaign_fields`), so invalid input fails
  as a clean 4xx instead of an unhandled `IntegrityError` surfacing as a 500.
- **Async/sync:** the entire stack is synchronous. No `async def` anywhere
  in `app/`. Do not introduce async/await without confirming this is an
  intentional architecture change first.
- **Import conventions:** absolute imports rooted at `app.` everywhere,
  never relative imports. `TYPE_CHECKING`-guarded imports for
  forward-reference-only type hints in ORM models.
- **Test conventions:** fixture-based (pytest, function-scoped by default),
  factory fixtures return a `make(...)` callable, test names are full
  sentences describing the scenario (e.g.
  `test_cross_tenant_creator_is_rejected_by_composite_fk`).
- **Documentation expectations:** every module/class/public function has a
  docstring explaining *why*, not just *what*. Inline comments are reserved
  for non-obvious rationale, security notes, or TODOs.

---

## 14. GIT STATE

- **Current branch:** `main`, tracking `origin/main` (no divergence at the
  time of writing — verify with `git status -sb` before assuming this still
  holds).
- **Latest commit on `main`:** `6f98356` — "Add tenant-safe role management".
- **Working tree:** the local RBAC removal and the entire Campaign
  Management Milestone 1 implementation described in this document exist
  only as **uncommitted working-tree changes** on top of `6f98356` at the
  time of writing — verify with `git status -sb` before assuming otherwise,
  since this may have been committed since.
- **Remote:** `origin` → `https://github.com/taranshrathore/verisure-ads-automation.git`
  (both fetch and push).
- **Ignored local configuration required to run the project** (per
  `.gitignore`): `.env` — must be created locally (not committed) with at
  minimum `DATABASE_URL`, `TEST_DATABASE_URL`, `JWT_SECRET_KEY` (required, no
  default), and the `POSTGRES_*` variables consumed by `compose.yaml`.
  `.venv/` is also gitignored (recreate via `uv sync`). The dedicated test
  database itself is not a file and is not tracked by git — it must be
  created manually on whatever PostgreSQL server `TEST_DATABASE_URL` points
  at (see README.md's "Testing" section).

---

## 15. OPERATING INSTRUCTIONS FOR THE NEXT SESSION

1. **Inspect files before modifying them.** Do not trust this document's
   prose over the actual current file contents — re-read any file you are
   about to change first, since this handoff is a snapshot and the repo may
   have moved on.
2. **Discuss and obtain approval for architecture before implementation,**
   especially for §12's two open tracks.
3. **Implement incrementally** — one focused capability per commit-sized
   change, matching this project's established pattern.
4. **Preserve repository/service/API boundaries** exactly as described in
   §2 and §10.
5. **Run migrations and tests after any schema or validation change:**
   `alembic upgrade head` against the dev DB, the equivalent against the
   test DB, then `pytest`. Also re-run the app import/`configure_mappers()`
   sanity check.
6. **Never commit or push unless explicitly instructed**, even after
   implementing and verifying a change.
7. **Report files changed, commands run, and results** after each phase, at
   the level of detail in this document.
