# fleetflow-api: Memory (progress ledger)

Factual record only. Update it after every substantial task, dependency or architecture change, and branch merge. Never record unrun tests as passed. Keep entries short, and newest first within each section.

## Project status

| Field          | Value                                                                          |
| -------------- | ------------------------------------------------------------------------------ |
| Application    | fleetflow-api (backend)                                                        |
| Current phase  | Phase 2: Core Logistics Entities (planning; Phase 1 completed 2026-11-23)         |
| Current branch | `docs/phase-1-complete`                                                        |
| Overall status | Phase 1 COMPLETED (PRs #2–#5 merged, owner checkpoint passed). Phase 2 planning. |
| Last updated   | 2026-11-23                                                                     |

## Completed work

| Date       | Branch             | Work                                                                                        | Files                                                                                                                       | Tests run      | Result |
| ---------- | ------------------ | ------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------- | ------ |
| 2026-11-22 | feature/api-users | Phase 1 PR 4/4: users API (list, detail, invite, patch) with ownership guards and company-row lock; `HasRequiredPermission` (method → codename, 405 for unimplemented methods); cursor pagination by public ID; `@idempotent` + `IdempotencyRecord` + daily purge; tenant isolation sweep; CI gates (overall coverage ≥ 85 %, security-critical modules ≥ 95 %, CHANGELOG entry on schema change); Phase 1 security review fixes (service-level tenant check in `update_user`, production JWT key ≥ 32 chars) | apps/accounts/\*, apps/core/idempotency.py, apps/core/models.py, apps/core/pagination.py, apps/core/tasks.py, tests/test_tenant_sweep.py, .github/workflows/ci.yml, config/settings/\*, CHANGELOG.md, openapi/schema.yaml | ruff, mypy, makemigrations --check, pytest (197 tests, 3 consecutive full runs green), coverage gates (overall 98 %, critical modules 99 %), pip-audit, schema validate; mutation checks: removing the company lock fails the owner-race test 3/3, removing tenant scoping fails the sweep | All pass locally. CI pending |
| 2026-11-22 | feature/api-auth (merged, PR #4) | Phase 1 PR 3/4: login, refresh (rotation, 30 s reuse grace, family revocation), logout, password reset and confirm (also completes invites), `/me`; JWT authentication through the token family; login throttle and cross-IP lockout; reset/refresh throttles; daily token purge task; JSON 400/403 Django handlers; CHANGELOG.md | apps/accounts/\*, apps/core/errors.py, apps/core/views.py, apps/core/serializers.py, config/\*, CHANGELOG.md, openapi/schema.yaml | ruff, mypy, makemigrations --check, pytest (136 tests, 99 % coverage, auth modules 100 %), pip-audit, schema validate; mutation checks (grace window, family revocation, lockout, company suspension each fail tests when broken); manual end-to-end in Compose (invite → set password → login → `/me` → refresh → reuse after 31 s → logout; 426 via `DRIVER_APP_MIN_VERSION`) | All pass locally. CI pending |
| 2026-11-22 | feature/api-foundation (merged, PR #3) | Phase 1 PR 2/4: error registry and envelope (`core/errors.py`, DRF exception handler, JSON 404/500), request ID middleware, 426 app-version middleware, `PublicIdModel` (UUIDv7), tenancy (`Company`, `Branch`, `TenantScopedModel`), custom `User` (email login, one company), `Role`/`Permission` seeded by data migration, append-only `AuditLog` + `audit.services.record`, invite email task, `create_company` command; removed Django admin, sessions, messages; DRF JSON-only with no implicit Session/Basic auth | apps/core/\*, apps/tenancy/\*, apps/audit/\*, apps/accounts/\*, config/settings/\*, config/urls.py, .env.example, pyproject.toml | ruff, mypy, makemigrations --check, pytest (80 tests, 98 % coverage), pip-audit, schema validate; manual: `create_company` in Compose, invite delivered by worker, duplicate email rejected | All pass locally. CI pending |
| 2026-11-22 | chore/python-3.14 (merged, PR #2) | Runtime moved to Python 3.14 (host 3.14.6, image `python:3.14-slim` = 3.14.10); lockfile re-resolved | .python-version, pyproject.toml, uv.lock, Dockerfile, docs | ruff, mypy, makemigrations --check, pytest (13 tests), pip-audit, schema validate; `docker compose up` healthy on 3.14 | All pass locally. CI pending |
| 2026-11-22 | feature/api-setup (merged, PR #1) | Phase 0 technical setup: uv project, settings split, Docker Compose (api, worker, beat, PostGIS, Redis), health endpoints, `apps/core` (abstract `TimeStampedModel`, PostGIS extension migration), drf-spectacular empty schema, GitHub Actions CI | pyproject.toml, uv.lock, Dockerfile, compose.yaml, .env.example, config/\*, apps/core/\*, openapi/schema.yaml, .github/workflows/ci.yml, README.md | ruff, mypy, makemigrations --check, pytest (13 tests), pip-audit, schema validate; manual `docker compose up` and DB-stop 503 check | All pass locally; CI green on PR #1 and on `main` after merge |
| 2026-11-21 | main | Workspace and documentation setup: repo initialized, docs written, Claude skills configured | CLAUDE.md, architecture.md, phases.md, rules.md, memory.md, skills.md, .claude/settings.json, .claude/skills/\*, .gitignore | None (no code) | n/a    |

## Current work

| Task                         | Branch             | Status                | In progress | Blockers                                  |
| ---------------------------- | ------------------ | --------------------- | ----------- | ----------------------------------------- |
| Phase 1 PR 1/4: Python 3.14 + Phase 0 completion docs | chore/python-3.14 | Merged (PR #2) | — | — |
| Phase 1 PR 2/4: errors, request ID, app version, tenancy, accounts models, RBAC seed, audit, `create_company` | feature/api-foundation | Merged (PR #3) | — | — |
| Phase 1 PR 3/4: auth flows, throttles, lockout, `/me` | feature/api-auth | Merged (PR #4) | — | — |
| Phase 1 PR 4/4: users API, idempotency, tenant sweep, CI gates | feature/api-users | Merged (PR #5) | — | — |
| Phase 1 checkpoint (owner review of the auth flow and security review results) | — | Passed 2026-11-23 | — | — |
| Phase 2 plan | — | Being prepared | — | Owner approval of the plan |

## Decisions

| ID      | Decision                                                                                                                                                                | Context                                               | Alternatives                       | Reason                                                                                                                                                        | Date       | Branch | Status           | Impact                                    |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | ---------------- | ----------------------------------------- |
| D-WS-1  | Three independent git repos in one workspace folder, no root CLAUDE.md                                                                                                  | Owner instruction                                     | Monorepo                           | Independent release cycles and history                                                                                                                        | 2026-11-21 | main   | APPROVED (owner) | Cross-app changes = separate PRs per repo |
| D-WS-2  | Folder names fleetflow-api / fleetflow-admin / fleetflow-driver-app                                                                                                     | Owner choice                                          | backend/admin-panel/mobile-app     | Industry-standard naming                                                                                                                                      | 2026-11-21 | main   | APPROVED (owner) | All docs use these names                  |
| D-API-0 | Modular monolith (Django + DRF)                                                                                                                                         | architecture §3                                       | Microservices                      | Single team, transactional core                                                                                                                               | 2026-11-21 | main   | PROPOSED         | —                                         |
| D-API-1 | Django 5.2 LTS (installed 5.2.19)                                                                                                                                       | simplejwt/django-storages classifiers stop at 5.2/5.1 | Django 6.1                         | Ecosystem compatibility, LTS until 2028-04                                                                                                                    | 2026-11-22 | feature/api-setup | APPROVED (owner) | Version pins in Phase 0                   |
| D-API-2 | Admin auth = JWT through the Next.js BFF in httpOnly cookies                                                                                                            | architecture §7                                       | Django session plus CSRF           | One auth path for both clients                                                                                                                                | 2026-11-22 | feature/api-setup | APPROVED (owner) | Affects fleetflow-admin (recorded in its memory.md) |
| D-API-3 | Shared-schema tenancy with company_id                                                                                                                                   | architecture §9                                       | schema-per-tenant, DB-per-tenant   | Simplicity at MVP scale                                                                                                                                       | 2026-11-21 | main   | PROPOSED         | —                                         |
| D-API-4 | Realtime: Channels WS for admin only; driver uses REST plus push                                                                                                        | architecture §17                                      | WS for both, SSE, polling          | Mobile reliability and battery                                                                                                                                | 2026-11-21 | main   | PROPOSED         | Affects both clients                      |
| D-API-5 | OpenAPI (drf-spectacular) as the contract source; clients generate types                                                                                                | architecture §25                                      | Hand-written types, shared package | Single source of truth across 3 repos                                                                                                                         | 2026-11-21 | main   | PROPOSED         | Both clients                              |
| D-API-6 | Hosting target deferred to Phase 11 | architecture §24 | AWS ECS/RDS; Render/Railway/Fly | Not needed for local Compose and CI | 2026-11-22 | feature/api-setup | APPROVED (owner) | Production image hardening (gunicorn etc.) waits for it |
| D-API-7 | uv manages Python and dependencies (`pyproject.toml` + `uv.lock`) | Phase 0 setup | pip-tools, Poetry | Fast, single tool, exact lockfile, installs the Python version | 2026-11-22 | feature/api-setup | APPROVED (owner, Phase 1 checkpoint 2026-11-23) | CI and Dockerfile use uv |
| D-API-8 | Plain `postgresql` DB engine until Phase 2; PostGIS extension enabled by `core/0001_postgis` migration | Phase 0 smoke test needs PostGIS, not GeoDjango | GeoDjango engine now | Avoids GDAL/GEOS in image and CI until the first geometry field | 2026-11-22 | feature/api-setup | APPROVED (owner, Phase 1 checkpoint 2026-11-23) | Phase 2 switches to `django.contrib.gis.db.backends.postgis` and installs GDAL |
| D-API-9 | Object storage service (MinIO) left out of Phase 0 Compose | MinIO stopped publishing community Docker images in late 2025; no storage code until Phase 3 | Pin an old MinIO image | Don't pin an unmaintained image for an unused service | 2026-11-22 | feature/api-setup | APPROVED (owner, Phase 1 checkpoint 2026-11-23) | Choose the local S3-compatible server in Phase 3 with django-storages |
| D-API-10 | Python 3.14 runtime | UUIDv7 public IDs (architecture §6) need `uuid.uuid7()`, stdlib only from 3.14 | Stay on 3.13 + `uuid-utils` | No extra dependency; all deps resolve to 3.14 wheels (dry run 2026-11-22) | 2026-11-22 | chore/python-3.14 | APPROVED (owner) | Dockerfile, CI, .python-version |
| D-API-11 | Phase 1 decisions: one company per user; companies created by `create_company` management command; 426 min app version in Phase 1; 30 s refresh-reuse grace window; RBAC in DB tables; minimal users API (list, detail, invite, patch) | Phase 1 planning | Multi-company membership; Django admin or public signup; strict reuse; code-defined roles; test-only views | See approved Phase 1 plan | 2026-11-22 | chore/python-3.14 | APPROVED (owner) | Admin and driver-app auth contract (recorded when PR 3 lands) |
| D-API-12 | No Django admin, sessions, messages, superuser or staff flag; custom `User` extends `AbstractBaseUser` only | JWT-only API, RBAC is FleetFlow's own (D-API-11) | Keep admin for platform staff | Less attack surface; Django's permission tables would duplicate RBAC | 2026-11-22 | feature/api-foundation | APPROVED (owner, Phase 1 checkpoint 2026-11-23) | `createsuperuser` is not available; onboarding is `create_company`. API docs served only where `API_DOCS_ENABLED` (local) |
| D-API-13 | Phase 1 plan adjustments: `TenantScopedModel` lives in `tenancy`; permission codenames, `HasPermission`, cursor pagination and `TokenFamily` land in the PR that first uses them (3 or 4) instead of PR 2; `factory-boy` added in PR 2 because its tests need it | Implementation of PR 2 | Follow the plan literally | Keeps the dependency direction clean and avoids unused code | 2026-11-22 | feature/api-foundation | APPROVED (owner, Phase 1 checkpoint 2026-11-23) | None on contracts |
| D-API-14 | Own session tracking instead of simplejwt `token_blacklist`: `TokenFamily` + `RefreshTokenRecord` (jti only); access tokens authenticated through a non-revoked family (one query); `User.sessions_revoked_at` removed | simplejwt's `OutstandingToken` stores full refresh tokens, and its blacklist error cannot tell reuse apart; second-precision `iat` let a token from the same second as a password reset survive | Use `token_blacklist`; keep the `iat` cut-off | No replayable tokens at rest; logout, reuse and reset end access immediately; same query count per request | 2026-11-22 | feature/api-auth | APPROVED (owner, Phase 1 checkpoint 2026-11-23) | Clients: `auth.token_invalid` after logout or reset means log in again |
| D-API-15 | Auth endpoints answer with trailing slashes (`/api/v1/auth/login/`) | Consistency with architecture §6 resource paths | Slashless | One convention for every path | 2026-11-22 | feature/api-auth | APPROVED (owner, Phase 1 checkpoint 2026-11-23) | Driver-app docs listed `/api/v1/auth/login` (recorded in its memory.md) |
| D-API-16 | Idempotency stores only successful responses; failed requests roll back their record | Architecture §20 does not specify which statuses are stored | Store and replay 4xx too (Stripe-style) | Failed requests have no side effects, so re-running them is equivalent and simpler; the record shares the mutation's transaction | 2026-11-22 | feature/api-users | APPROVED (owner, Phase 1 checkpoint 2026-11-23) | Clients may retry a failed request with the same key |
| D-API-17 | Users API ownership rules: no self-change of roles, branches or status; only owners grant or revoke owner or change an owner's access; at least one active owner; edits serialized by a company-row lock | Privilege-escalation and lock-out risks in user management | Fewer guards | Prevents escalation, self-lockout and the "two owners demote each other" race (tested) | 2026-11-22 | feature/api-users | APPROVED (owner, Phase 1 checkpoint 2026-11-23) | Admin users screen must surface these error codes |
| D-WS-3  | Engineering standards added: principles and definition of done in CLAUDE.md; enforceable detail merged into rules.md §1–5, §8 and new §10 (constants and configuration) | Owner-supplied engineering standards                  | Paste the full text into CLAUDE.md | Avoids duplicating existing rules and keeps CLAUDE.md a short entry point; frontend-only items (TypeScript, ESLint, UI styling) omitted for this backend repo | 2026-11-21 | main   | APPROVED (owner) | All future API work                       |

## Dependencies added/changed

Installed 2026-11-22 on `feature/api-setup` (exact pins in `uv.lock`):

| Package | Version | Group | Reason |
|---|---|---|---|
| django | 5.2.19 | runtime | Framework (D-API-1) |
| djangorestframework | 3.18.3 | runtime | API layer |
| drf-spectacular | 0.30.0 | runtime | OpenAPI contract (D-API-5) |
| psycopg[binary] | 3.3.9 | runtime | PostgreSQL driver |
| django-environ | 0.14.0 | runtime | Env-based settings |
| celery[redis] | 5.7.0 | runtime | Worker and beat processes |
| redis | 8.1.0 | runtime | Readiness check, Celery broker client |
| uvicorn | 0.54.0 | runtime | ASGI server |
| argon2-cffi | 25.1.0 | runtime | Argon2 password hashing (architecture §23) |
| ruff | 0.17.6 | dev | Lint and format |
| mypy | 1.19.1 | dev | Type check. Held below 2.x by django-stubs 5.2.x `compatible-mypy` |
| django-stubs / djangorestframework-stubs | 5.2.9 / 3.16.9 | dev | Types matching Django 5.2 |
| pytest / pytest-django / pytest-cov | 9.2.0 / 4.14.0 / 7.1.0 | dev | Tests and coverage |
| djangorestframework-simplejwt (+ PyJWT) | 5.5.1 (2.15.1) | runtime | JWT signing and verification only (D-API-14); declares Django 5.2 support, MIT, maintained by Jazzband. Added 2026-11-22 on `feature/api-auth` |
| factory-boy (+ Faker) | 3.3.3 (40.63.0) | dev | Model factories (architecture §26.1); added 2026-11-22 on `feature/api-foundation` |
| pip-audit | 2.10.1 | dev | Dependency vulnerability audit |

Deferred to the phase that needs them: simplejwt, factory_boy (Phase 1); structlog, sentry-sdk (Phase 1 or later); django-storages, boto3 (Phase 3); channels (Phase 7); gunicorn (Phase 11).

Images: `postgis/postgis:18-3.6`, `redis:8.2-alpine`, `python:3.14-slim` (Python 3.13 until 2026-11-22, D-API-10), uv 0.12.1.

## Incoming client requests

Requests from the client apps, to be designed into the relevant API phase (details are in each client's memory.md):
| From | Request | Target phase | Status |
|---|---|---|---|
| driver-app | `POST /api/v1/devices/` push token register/unregister | 9 (or earlier) | Proposed |
| driver-app | Minimum app version enforcement (426 on `X-App-Version`) | 1 | Proposed |
| driver-app | 409 `stop.reassigned`; accept a late POD as evidence (`late_reassigned`) | 5 | Proposed |
| driver-app | Shift start/end/break and vehicle inspection endpoints | 3 | Proposed |
| admin | WS ticket endpoint via BFF (already in architecture §17) | 7 | In design |

## Branch history

### main

- 2026-11-21: Repo initialized. Phase 0 documentation committed (`6b04c1c`).

### feature/api-setup

- Status: Merged to `main` (PR #1, `1c58f01`). Work: Phase 0 technical setup. Tests: 13 pass; CI green on the PR and on `main`.

### chore/python-3.14

- Status: Merged to `main` (PR #2, `25e749b`). Work: Phase 1 PR 1/4, runtime to Python 3.14 and Phase 0 completion docs.

### feature/api-foundation

- Status: Merged to `main` (PR #3, `c40a174`). Work: Phase 1 PR 2/4. CI green on the PR.

### feature/api-auth

- Status: Merged to `main` (PR #4, `ba758fc`). Work: Phase 1 PR 3/4. CI first failed on a schema diff (operation IDs depended on mounted routes); fixed by pinning `SCHEMA_PATH_PREFIX` (`5328353`), then green.

### feature/api-users

- Status: Merged to `main` (PR #5, `ff45aca`). Work: Phase 1 PR 4/4. CI green on the PR.

### docs/phase-1-complete

- Status: Ready for PR. Work: mark Phase 1 COMPLETED and record the owner's approval of D-API-7–9 and D-API-12–17.


## Known issues

| Issue                                                                                            | Severity | Branch | Status | Resolution                           |
| ------------------------------------------------------------------------------------------------ | -------- | ------ | ------ | ------------------------------------ |
| djangorestframework-simplejwt 5.5.1 and django-storages 1.14.6 do not declare Django 6.x support | Medium   | —      | Resolved | D-API-1: Django 5.2 LTS chosen |
| PostGIS image tag for PG18 not yet pinned                                                        | Low      | feature/api-setup | Resolved | Pinned `postgis/postgis:18-3.6` |

## Security reviews

### Phase 1 checkpoint (2026-11-22, `fleetflow-security-review` checklist, whole Phase 1 diff)

| Finding | Severity | Status |
|---|---|---|
| `accounts.services.update_user` trusted the view to pass a same-company target (rules.md §3 asks for a service-level check too) | Medium | Fixed: service raises `not_found` for another company's user (tested) |
| A short `JWT_SIGNING_KEY` would allow offline brute-forcing of HS256 tokens | Low | Fixed: production refuses keys under 32 characters (tested) |
| `POST /users/` answers `409 user.email_taken` for an email registered in *any* company, so `users.manage` holders can learn that an address uses FleetFlow elsewhere | Low | Accepted: inherent to global email logins (D-API-11); revisit if multi-company membership is ever adopted |
| `PASSWORD_RESET_TIMEOUT` is Django's default of 3 days and applies to both reset and invite links | Info | Accepted for MVP: invites need a multi-day window; revisit with separate invite tokens if needed |

Checked with no finding:
- tenant scoping of every selector, and the sweep covers every object route;
- the company is never taken from request input;
- explicit serializer fields;
- no secrets or tokens in logs or error bodies;
- throttles on login, refresh and password reset;
- enumeration-safe login, lockout and reset;
- `select_for_update` on contended rows;
- audit entries for login, logout, reuse, lockout, reset and user changes;
- `pip-audit` clean.

Not applicable yet: driver-scoped endpoints, public tracking, uploads, exports, OTP.

## Next steps

1. Merge `docs/phase-1-complete`.
2. Owner approves the Phase 2 plan (customers, addresses, shipments, status machine, tracking events, public tracking token), then implementation starts.
