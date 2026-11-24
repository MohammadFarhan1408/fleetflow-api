# fleetflow-api: Memory (progress ledger)

Factual record only. Update it after every substantial task, dependency or architecture change, and branch merge. Never record unrun tests as passed. Keep entries short, and newest first within each section.

## Project status

| Field          | Value                                                                          |
| -------------- | ------------------------------------------------------------------------------ |
| Application    | fleetflow-api (backend)                                                        |
| Current phase  | Phase 3: Drivers & Fleet (IN PROGRESS, PR 1 of 4 on `feature/api-uploads`)    |
| Current branch | `feature/api-uploads`                                                          |
| Overall status | Phases 0–2 COMPLETED. Phase 3 IN PROGRESS: plan approved 2026-11-24, PR 1 (uploads) pushed for review. |
| Last updated   | 2026-11-24                                                                     |

## Completed work

| Date       | Branch             | Work                                                                                        | Files                                                                                                                       | Tests run      | Result |
| ---------- | ------------------ | ------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------- | ------ |
| 2026-11-24 | feature/api-uploads | Phase 3 PR 1/4. Object storage and the §19 upload flow. Compose and CI run SeaweedFS (`chrislusf/seaweedfs:4.55`, dev keys in `docker/seaweedfs-s3.json`); `core/storage.py` is the only boto3 user (presign PUT with signed size and type, head, ranged read, put, delete, presigned GET as attachment, `bucket_ok` for `/health/ready`). New `uploads` app: `Upload` (server-built object key, ETag fingerprint, DB checks for type, size, confirmed and attached state); `POST /uploads/` (60/h per user) and `POST /uploads/{id}/confirm/` (uploader only: size, magic bytes, lossless JPEG APP1/APP13 and PNG eXIf/tEXt/iTXt/zTXt stripping, rejected files deleted and audited); `attach_upload` and `presign_download` for domain services, both re-checking the ETag; hourly `purge_unattached_uploads`; `ensure_upload_bucket` command. `uploads.create` seeded for owner, admin, ops_manager, fleet_manager. Error codes `upload.*` and `storage.unavailable` (503). Docs: architecture §2, §4, §5, §19, §20, §21; phases.md; CHANGELOG | apps/uploads/\*, apps/core/{storage,errors,views}.py, apps/audit/models.py + 0005, config/settings/base.py, config/urls.py, compose.yaml, docker/seaweedfs-s3.json, .github/workflows/ci.yml, .env.example, conftest.py, tests/test_tenant_sweep.py, pyproject.toml, uv.lock, openapi/schema.yaml, CHANGELOG.md, docs | All inside the API container: ruff, mypy, `makemigrations --check`, pytest (709 tests against the real SeaweedFS; 99.26 % coverage; critical modules 99 %, `uploads/services.py` and `uploads/files.py` 100 %), pip-audit (clean), schema validate and in sync (209 lines added, none removed). Migrations applied, reversed (`uploads zero`, `audit 0004`) and reapplied on the dev DB. Five mutation checks each failed a test (uploader filter, ETag swap check, magic-byte check, stripping, size limit at confirm); the magic-byte one first survived and gained a PDF test. Manual Compose run with curl: presigned host `localhost:8333`, PUT 200, confirm of a JPEG with GPS EXIF 200 and the stored object holds no GPS bytes, an executable renamed `.jpg` 400 `upload.type_mismatch` with an audit entry, 25 MB declaration 400 `upload.too_large`, no token 401, anonymous bucket read 403, `/health/ready` shows `storage: ok` | All pass locally. CI pending (the new SeaweedFS step could only be checked on the PR) |
| 2026-11-24 | docs/phase-2-complete | Phase 2 checkpoint: acceptance verified, Phase 2 marked COMPLETED, status enum sign-off recorded (phases.md, memory.md, architecture §25) | phases.md, memory.md, architecture.md | On `main` at `693420c`, inside the API container: ruff, mypy, `makemigrations --check`, pytest (597 passed, 99.1 % coverage; critical modules 99 %), schema validate and in sync, pip-audit clean. CI on `main` green for the merges of PRs #7, #8 and #9 (one read of the Actions API). Acceptance mapping: creation table → `apps/shipments/tests/test_create_api.py` (COD deferred, D-API-23; webhook row is Phase 9); transition table → `test_transitions.py` (out-of-order flag deferred to Phase 5, D-API-24); single winner → `test_concurrent_transitions_have_exactly_one_winner`; consistency check → `apps/shipments/tasks.py` with tests | Phase 2 COMPLETED |
| 2026-11-24 | feature/api-shipment-status (merged, PR #9) | Phase 2 PR 3/3. Status machine: `ALLOWED_TRANSITIONS` (every §11 edge), `transition()` with row lock, `expected_status` (409 `status_conflict`), edge permissions and deny-by-default for unmapped edges; confirm re-validates the draft; `correct_status()` (any live status, non-public `correction` event, audited). APIs: transitions (confirm, cancel, recovery), corrections, timeline, public tracking by token (owner-approved fields, 60/min per IP, credentials ignored). Daily consistency task (log + audit, no auto-fix). `TrackingEvent.reason`. Permissions `shipments.cancel`/`recover`/`correct_status` seeded. hypothesis (dev) added | apps/shipments/\*, apps/core/{errors,pagination}.py, apps/audit/models.py + 0004, config/settings/base.py, apps/accounts/tests/{test_auth,test_models}.py, tests/test_tenant_sweep.py, pyproject.toml, uv.lock, openapi/schema.yaml, CHANGELOG.md, docs | All inside the API container: ruff, mypy, `makemigrations --check`, pytest (597 tests incl. every allowed and disallowed edge, a 2-thread single-winner test and a hypothesis property test; 99 % coverage; critical modules 99 %), pip-audit (clean), schema validate (additive only vs `main`). Migrations applied, reversed and reapplied on the dev DB. Manual Compose run with curl: public page before; cancel without reason 400; cancel 200; repeat with stale status 409; recovery 200; correction to `delivered` 200; timeline shows 4 events with the correction non-public; public page shows `delivered` with public events only; bad token 404 | All pass locally. CI pending |
| 2026-11-23 | feature/api-shipments (merged, PR #8) | Phase 2 PR 2/3. Shipments: new `shipments` app with `Shipment` (branch, sender customer, service type, `FF-<BRANCH>-<seq>` tracking number from a locked per-branch counter, 128-bit rotatable tracking token, priority, scheduled pickup date, unique external reference, cached total weight), `ShipmentItem` (one per parcel, barcode `<tracking number>-NNN`), immutable `ShipmentAddress` snapshots (copied from the address book or typed in) and append-only `TrackingEvent` (first `created` event). APIs: list with filters and branch scope, create (`Idempotency-Key` required, optional draft), detail, draft PATCH/DELETE, token rotation. `shipments.view`/`create`/`all_branches` seed. Shared code: `PostalAddress` abstract model (customers, no schema change), append-only guard moved to `core`, `idempotency_required`. Onboarding: `create_company` also creates the first branch (`--branch-name`, `--branch-code`) with the owner as a member | apps/shipments/\*, apps/core/{models,errors,idempotency}.py, apps/customers/{models,services}.py, apps/audit/models.py + 0003, apps/tenancy/{services,factories}.py, apps/accounts/services.py, apps/accounts/management/commands/create_company.py, config/settings/base.py, config/urls.py, tests/test_tenant_sweep.py, openapi/schema.yaml, CHANGELOG.md, docs | All inside the API container: ruff, mypy, `makemigrations --check` (no change for `customers` after the refactor), pytest (402 tests, 99.0 % coverage; shipments services 99 %), pip-audit (clean), schema validate (additive only, compared with `main` component by component). Migrations applied, reversed and reapplied on the dev DB; seeded role table checked. Manual Compose run with curl: create company with branch `ber1` → `BER1`; create without key 400; create → `FF-BER1-000001`, barcode `-001`, pickup contact from the customer, delivery `manual`; replay 201 with `Idempotent-Replayed`; duplicate reference 409; draft PATCH, token rotation 200, draft DELETE 204; filtered list | All pass locally. CI pending |
| 2026-11-23 | feature/api-geo-customers (merged, PR #7) | Phase 2 PR 1/3. Infrastructure: GeoDjango engine, with `gdal-bin` in the image and CI. Tenancy: `Company.currency` (ISO 4217) and `timezone` (IANA); `Branch.code`; per-company `ServiceType` seeded by `create_company` and backfilled by a data migration. `create_company` requires `--currency` and `--timezone`. Customers: new `customers` app with `Customer` and `Address` (E.164, ISO country, optional manual coordinates); list/create/detail/update/archive APIs; `customers.view`/`customers.manage` seed; audit entries for create and archive. Cleanup: `current_user` moved to `accounts.authentication`, and `client_for` to the accounts test conftest | apps/tenancy/\*, apps/customers/\*, apps/accounts/{authentication,services,views}.py, apps/accounts/management/commands/create_company.py, apps/core/errors.py, apps/audit/models.py + 0002, config/settings/base.py, config/urls.py, Dockerfile, .github/workflows/ci.yml, tests/test_tenant_sweep.py, openapi/schema.yaml, CHANGELOG.md, docs | All inside the API container: ruff, mypy, `makemigrations --check`, pytest (293 tests, 98.9 % coverage; critical modules 99 %), pip-audit (clean), schema validate. Migrations: applied to a dev DB holding Phase 1 data (2 companies, 1 branch → USD/UTC, `B1`, 3 service types each), reversed to Phase 1 state and reapplied. Manual Compose run: `create_company --currency eur --timezone Europe/Berlin` seeds 3 service types, and an invalid currency is rejected. Login, create a customer (idempotent replay 201), add an address with coordinates (`manual`), archive the customer (the address archive cascades, so a later address PATCH gets 409) | All pass locally. CI pending |
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
| Phase 2 plan | — | Approved 2026-11-23 (phases.md, D-API-18 to D-API-21) | — | — |
| Phase 2 PR 1/3: geo engine, company currency/timezone, branch code, service types, customers and addresses | feature/api-geo-customers | Merged (PR #7) | — | — |
| Phase 2 PR 2/3: shipments, items, address snapshots, tracking numbers and token, create/list/detail, draft edit/delete, branch-scoping helper and `shipments.*` permissions (D-API-22) | feature/api-shipments | Merged (PR #8) | — | — |
| Phase 2 PR 3/3: status machine, manual transitions, timeline, consistency check, public tracking | feature/api-shipment-status | Merged (PR #9) | — | — |
| Phase 2 checkpoint (owner signs off the status enum before clients build on it) | docs/phase-2-complete | Passed 2026-11-24 (enum signed off, Phase 2 COMPLETED) | — | — |
| Phase 3 plan (drivers & fleet, first uploads) | — | Approved 2026-11-24 (phases.md, D-API-25) | — | — |
| Phase 3 PR 1/4: storage, uploads app, upload flow | feature/api-uploads | Pushed, awaiting owner merge | — | — |
| Phase 3 PR 2/4: drivers, vehicles, leave, status, availability, branch-scope refactor | feature/api-fleet | Not started | — | Needs PR 1 merged |
| Phase 3 PR 3/4: driver and vehicle documents, expiry, license and vehicle checks | feature/api-fleet-documents | Not started | — | Needs PRs 1 and 2 merged |
| Phase 3 PR 4/4: shifts, `/me/driver/`, driver profile rule | feature/api-driver-shifts | Not started | — | Needs PR 2 merged |

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
| D-API-18 | No geocoding provider in Phase 2: addresses take optional client-supplied coordinates with `geocode_confidence` `manual` or `none`; a provider can be added later behind one service function | Provider was NEEDS APPROVAL | Google, Mapbox, Nominatim | No paid service or dependency until needed | 2026-11-23 | docs/phase-1-complete | APPROVED (owner) | Routes (Phase 4) need coordinates; admin should offer a map pin |
| D-API-19 | Global operation: E.164 phone format check (no phonenumbers dependency), ISO 3166-1 alpha-2 country on addresses, free-text postal codes, per-company ISO 4217 currency and IANA timezone | Launch countries not fixed | Single-country strict validation | Works anywhere without per-country rules | 2026-11-23 | docs/phase-1-complete | APPROVED (owner) | `create_company` gains currency and timezone |
| D-API-20 | Per-company `ServiceType` table with seeded defaults; shipments snapshot pickup and delivery addresses into immutable `ShipmentAddress` rows | Service types and address history were open | Fixed enum; reference mutable address rows | Each carrier defines its own services; history never changes when the address book is edited | 2026-11-23 | docs/phase-1-complete | APPROVED (owner) | Admin service-type settings screen later |
| D-API-21 | Phase 2 scope rules. **Transitions:** only manual ones (confirm, cancel, recovery, admin correction) are exposed in Phase 2. **Branch scoping:** shipments belong to an origin branch; users see their own branches unless they hold `shipments.all_branches`. **Shape:** one `ShipmentItem` per parcel (no quantity), 1–500 items, total weight above 0 and up to 30,000 kg; only drafts are editable. **Dependencies:** django-filter and hypothesis (dev). **Checks run inside the API container** (GDAL). **Role permissions:** owner and admin get everything; ops_manager everything except `service_types.manage`; dispatcher customers.view plus shipments view, create and cancel (own branches); customer_service customers view and manage plus shipments view, create and cancel; warehouse_staff and fleet_manager shipments.view; finance and viewer customers.view and shipments.view; driver nothing yet | Phase 2 planning | Expose all transitions now; quantity per item | Later phases own their transitions' preconditions (scans, routes, POD) | 2026-11-23 | docs/phase-1-complete | APPROVED (owner) | Admin and driver apps build on the status enum (signed off by the owner 2026-11-24) |
| D-API-22 | Phase 2 PR 1 adjustments. **Scope:** the branch-scoping helper and `shipments.*` permissions move to PR 2, and `service_types.manage` to the PR that adds its endpoint. They land where they are first used, as in D-API-13. **ServiceType placement:** `ServiceType` lives in `tenancy`, because `create_company` seeds it and tenancy cannot import higher modules. **Seed values:** `standard` and `express` (30 kg, 120 × 80 × 80 cm) and `freight` (1000 kg, 240 × 120 × 220 cm), all per parcel. **Validation:** full ISO 4217 (circulating) and ISO 3166-1 alpha-2 code lists, with no dependency. **Backfill:** existing companies get USD/UTC and existing branches get `B<id>`; only dev data existed (D-API-6) | Unused code in PR 1; module dependency direction | Follow the plan literally; ServiceType in `shipments` | No speculative code; keeps architecture §5 intact | 2026-11-23 | feature/api-geo-customers | APPROVED (owner, scope and seed values 2026-11-23) | A real DB would need correct currency/timezone set before migrating |
| D-WS-3  | Engineering standards added: principles and definition of done in CLAUDE.md; enforceable detail merged into rules.md §1–5, §8 and new §10 (constants and configuration) | Owner-supplied engineering standards                  | Paste the full text into CLAUDE.md | Avoids duplicating existing rules and keeps CLAUDE.md a short entry point; frontend-only items (TypeScript, ESLint, UI styling) omitted for this backend repo | 2026-11-21 | main   | APPROVED (owner) | All future API work                       |
| D-API-23 | Phase 2 PR 2 choices (owner 2026-11-23). **Fields:** priority (`normal`/`high`/`urgent`), `scheduled_pickup_date` (not before today in the company timezone) and `external_reference` (unique per company when set); COD deferred to billing because precision needs per-currency minor units. **Addresses:** pickup and delivery are each `{"address_id"}` (any active address-book entry of the company) or an inline address; both are snapshotted; a copied entry without a contact uses the customer's name. **Tracking token:** stored as is and shown to `shipments.view` in detail, rotatable. **Implementation choices:** drafts get a tracking number but no TrackingEvent (draft deletion never touches the append-only history; numbers of deleted drafts leave gaps); `shipments.create` also covers draft edit/delete and token rotation; `shipments.all_branches` only for owner, admin, ops_manager (least privilege; D-API-21 named only dispatchers as own-branch); django-filter not added (a query serializer covers the filters); `create_company` creates the first branch because no branch API exists | PR 2 planning | COD now; inline-only or address-book-only addresses; hashed token shown once; event on draft creation; django-filter | Smallest complete scope that satisfies the §26.4 creation table | 2026-11-23 | feature/api-shipments | APPROVED (owner, Phase 2 checkpoint 2026-11-24) | Admin builds the shipment form and list on this contract; `create_company` gains `--branch-name`/`--branch-code` |
| D-API-24 | Phase 2 PR 3 choices (owner 2026-11-24). **Public tracking fields:** tracking number, status, carrier (company name), service type name, delivery city and country, scheduled pickup date, public events (status and time); never names, phones, street address, sender or internal events; drafts and suspended companies 404. **Implementation choices (plan approved):** API transitions limited to confirm, cancel and recovery, all with `expected_status`; reason required for cancel, recovery and correction; corrections are non-public events and audited; unmapped edges denied unless the calling service passes `authorized_by_caller=True`; `TrackingEvent.reason` instead of a JSON `payload`; out-of-order flagging deferred to Phase 5 (no device times before then); consistency check compares the latest *recorded* status event, runs daily via beat, logs and audits, never fixes; public tracking lives in `shipments` until `tracking` exists (Phase 7) | PR 3 planning | All edges exposed now; payload JSON; hashed token | Smallest complete scope for the §26.4 transition table; least-privilege public data | 2026-11-24 | feature/api-shipment-status | APPROVED (owner, Phase 2 checkpoint 2026-11-24) | Admin builds status actions and the timeline; the public tracking page uses `/track/{token}/` |
| D-API-25 | Phase 3 plan (owner 2026-11-24). **Storage:** SeaweedFS 4.55 in Compose and CI; production is any S3 service through `S3_*` env vars; boto3 only, no django-storages (every operation is a client call and files never touch `FileField`), confined to `core/storage.py`. **Uploads:** types and limits as architecture §19 (JPEG and PNG ≤ 10 MB, PDF ≤ 20 MB); EXIF/XMP/IPTC stripped losslessly at confirm with stdlib code and no original kept (PDF metadata untouched, no malware scan); the generic `uploads` app sits between accounts and fleet. **Implementation choices in PR 1:** ETag fingerprint at confirm, re-checked by attach and download, because a presigned PUT URL outlives confirmation; upload creation is not idempotency-keyed (a retry only adds a pending row that the purge removes, and the stored response would hold a signed URL); no `upload.unsupported_type` code (a bad type or purpose is `validation.invalid` from the enum field); extra codes `upload.size_mismatch` and `storage.unavailable`; the storage client lives in `core` so `/health/ready` can use it. **Fleet:** `Driver.user` one-to-one with an active same-company user holding the `driver` role, invited first through the users API; drivers and vehicles have a required home branch with `fleet.all_branches` (owner, admin, ops_manager); a document is valid through its `expires_on` in the company timezone; license categories are the Vienna Convention set with exact membership; volume in m³; downloading document files needs `fleet.manage`. **Shifts:** start and end only (breaks deferred, inspections to Phase 8). **Login rule:** architecture §7 rejects a driver without an active profile at login, but accounts cannot import fleet; driver endpoints answer 403 `driver.profile_inactive` instead and §7 is reworded in PR 4 | Phase 3 planning | django-storages; Pillow re-encoding or keeping originals; MinIO, RustFS or Garage; company-wide fleet; check at login | Smallest complete scope for the §26.4 upload and assignment inputs; no unmaintained image; least privilege for personal data | 2026-11-24 | feature/api-uploads | APPROVED (owner, plan approved 2026-11-24) | Admin builds the fleet screens and the upload flow; driver-app uses `/me/driver/` and the shift endpoints (PR 4) |

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
| hypothesis | 6.170.17 | dev | Property tests of the shipment state machine (architecture §26.1, approved in D-API-21). MPL-2.0, actively released, no runtime use; lockfile adds no other package. Added 2026-11-24 on `feature/api-shipment-status` |
| boto3 (+ botocore, jmespath, s3transfer) | 1.44.10 (1.44.10, 1.1.0, 0.19.3) | runtime | S3 client for presigned URLs and object operations (D-API-25). Apache-2.0, maintained by AWS, in the lockfile with three small pure-Python transitive packages; `pip-audit` clean. Replaces the django-storages row of architecture §2. Added 2026-11-24 on `feature/api-uploads` |

No Python dependency added in Phase 2 PR 1. The image and CI gained the Debian/Ubuntu `gdal-bin` package (GDAL 3.10.3 and GEOS 3.13.1 in the image) for GeoDjango.

Deferred to the phase that needs them: structlog, sentry-sdk (Phase 1 or later); channels (Phase 7); gunicorn (Phase 11). django-storages is no longer planned (D-API-25).

Images: `postgis/postgis:18-3.6`, `redis:8.2-alpine`, `chrislusf/seaweedfs:4.55` (Apache-2.0; local and CI object storage, added 2026-11-24), `python:3.14-slim` (Python 3.13 until 2026-11-22, D-API-10), uv 0.12.1.

## Incoming client requests

Requests from the client apps, to be designed into the relevant API phase (details are in each client's memory.md):
| From | Request | Target phase | Status |
|---|---|---|---|
| driver-app | `POST /api/v1/devices/` push token register/unregister | 9 (or earlier) | Proposed |
| driver-app | Minimum app version enforcement (426 on `X-App-Version`) | 1 | Proposed |
| driver-app | 409 `stop.reassigned`; accept a late POD as evidence (`late_reassigned`) | 5 | Proposed |
| driver-app | Shift start/end/break and vehicle inspection endpoints | 3 | Designed: start and end in Phase 3 PR 4; breaks deferred, inspections go to Phase 8 (D-API-25) |
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

- Status: Merged to `main` (PR #6, `096aa21`). Work: mark Phase 1 COMPLETED and record the Phase 2 plan.

### feature/api-geo-customers

- Status: Merged to `main` (PR #7, `52d9ee1`). Work: Phase 2 PR 1/3.

### feature/api-shipments

- Status: Merged to `main` (PR #8, `2bfb8d1`). Work: Phase 2 PR 2/3.

### feature/api-shipment-status

- Status: Merged to `main` (PR #9, `693420c`). Work: Phase 2 PR 3/3. CI green on `main`.

### docs/phase-2-complete

- Status: Merged to `main` (PR #10, `4d52d79`). Work: Phase 2 checkpoint docs.

### feature/api-uploads

- Status: Pushed, awaiting owner review and merge. Work: Phase 3 PR 1/4.


## Known issues

| Issue                                                                                            | Severity | Branch | Status | Resolution                           |
| ------------------------------------------------------------------------------------------------ | -------- | ------ | ------ | ------------------------------------ |
| djangorestframework-simplejwt 5.5.1 and django-storages 1.14.6 do not declare Django 6.x support | Medium   | —      | Resolved | D-API-1: Django 5.2 LTS chosen |
| PostGIS image tag for PG18 not yet pinned                                                        | Low      | feature/api-setup | Resolved | Pinned `postgis/postgis:18-3.6` |

## Security reviews

### Phase 3 PR 1 (2026-11-24, `fleetflow-security-review` checklist, `feature/api-uploads` diff)

| Finding | Severity | Status |
|---|---|---|
| A presigned PUT URL stays valid after confirmation until it expires (5 min), so the uploader could replace a verified file with different content | Medium | Fixed in the design: confirm stores the object's ETag, and `attach_upload` and `presign_download` re-check it (409 `upload.not_confirmed`); tested by swapping the object after confirmation |
| The ETag is taken from the HEAD before the file is read, so a swap between HEAD and read is also caught later instead of being blessed | Info | Intended ordering, covered by the swap test |
| The local SeaweedFS keys are fixed development values committed in `compose.yaml`, `docker/seaweedfs-s3.json` and CI | Low | Accepted: local and CI only; the settings have no default for the S3 credentials, so production must provide its own |
| PDFs keep their embedded metadata and are not scanned for malware | Low | Accepted by the owner for now (D-API-25); malware scanning is a Phase 11 decision |

Checked with no finding:
- Access: every upload view uses `IsAuthenticated` plus `HasRequiredPermission`; only the uploader can confirm (another user in the same company and another company both get 404, and the confirm route is in the tenant sweep); the company comes from the user.
- Input: type and size are enforced by the signature at the store, again at confirm (size within the limit, equal to the declared size, magic bytes), and by DB check constraints; object keys are built from server values only; the presigned download forces `Content-Disposition: attachment` with a fixed file name.
- Integrity: rejected files are deleted and audited in a committed transaction before the 400; confirm locks the upload row; a storage outage leaves the upload retryable (503, no half state); the purge deletes the object before the row.
- Data: audit entries hold IDs, the reason and the content type only; storage errors are logged by class and operation, never to the client; `/health/ready` reports pass or fail only.
- Throttle: 60/h per user on creation, tested.
- Dependencies: boto3 only; `pip-audit` clean.

Not applicable yet: public tracking changes, OTP, exports, driver endpoints (PR 4).

### Phase 2 PR 3 (2026-11-24, `fleetflow-security-review` checklist, `feature/api-shipment-status` diff)

| Finding | Severity | Status |
|---|---|---|
| `transition()` allowed edges without a mapped permission to any actor who could see the shipment. The API serializer limits `to` today, but widening it later would have exposed every lifecycle edge to `shipments.view` holders | Medium | Fixed: unmapped edges are denied unless the calling service passes `authorized_by_caller=True` (tested) |
| Public tracking shows the current status even when the last change was a non-public correction, so the page can show a status without a matching public event | Info | Accepted: the current status must be truthful; correction details stay internal |

Checked with no finding:
- Public tracking: the only unauthenticated endpoint is the approved one; 128-bit token as the sole credential (unscoped lookup by design, documented); whitelisted fields only (tested, including that names, street and sender never appear); internal events hidden; rotated tokens, drafts and suspended companies 404; credentials ignored; throttled 60/min per IP (tested); excluded from the tenant sweep with a documented reason and covered by its own tests.
- Tenancy and scope: transitions, corrections and the timeline resolve the shipment through the branch-scoped selector and the services re-lock through it; all three object routes are in the tenant sweep.
- Integrity: status changes only in `transition`/`correct_status`, with `select_for_update` plus `expected_status` (2-thread single-winner test); every status write appends its event in the same transaction (hypothesis property test); `TrackingEvent` stays append-only; recovery and corrections audited.
- Data: audit entries hold IDs and statuses only; reasons are free text kept in events (staff-visible only).
- Dependencies: hypothesis is dev-only; `pip-audit` clean.

Not applicable: uploads, exports, OTP, driver endpoints.

### Phase 2 PR 2 (2026-11-23, `fleetflow-security-review` checklist, `feature/api-shipments` diff)

| Finding | Severity | Status |
|---|---|---|
| Two concurrent PATCHes of the same draft: the view merges the sent fields onto values read before the service takes the row lock, so the later request can overwrite the earlier one's other changes | Low | Accepted: drafts only, rare; add an optimistic `version` (as planned for routes, architecture §20) if the admin edits drafts collaboratively |

Checked with no finding:
- Tenancy: every read and the services' locked re-reads start from `for_company` (through `shipments_for_user` / `branches_for_user`); the company is never read from the body (tested); another company's shipment answers 404 and another company's customer, address, branch or service type in a body answers 400 without echoing it (tested); both object routes and the list are in the tenant sweep, which also checks the tracking token and reference never leak.
- Branch scope: list, detail, edit, delete, rotation and create honour the user's branches unless `shipments.all_branches` (tested).
- Access: `HasRequiredPermission` on every view, full role matrix tested; no unauthenticated endpoint.
- Data: explicit serializer fields; the tracking token appears only in detail and rotation responses, never in the list or the audit log; audit entries hold IDs and the status only.
- Integrity: tracking numbers from a locked counter (4-thread test); `Idempotency-Key` required on create; DB constraints mirror the weight, status, priority, reference, barcode and snapshot rules; `TrackingEvent` is append-only and PROTECTs its shipment.
- Dependencies: no new package; `pip-audit` clean.

Not applicable: uploads, exports, OTP, public tracking (PR 3), driver endpoints.

### Phase 2 PR 1 (2026-11-23, `fleetflow-security-review` checklist, `feature/api-geo-customers` diff)

| Finding | Severity | Status |
|---|---|---|
| Services re-read locked rows through the unscoped `.objects` manager after the company check (rules.md §2 asks for the tenant-scoped manager) | Low | Fixed: the re-reads start from `for_company(actor.company)` |

Checked with no finding:
- Tenancy:
  - every selector starts from `for_company`
  - the company comes from the user, never from request input (tested)
  - cross-tenant IDs give 404 in views and services (tested)
  - all 5 object routes and the customer list are in the tenant sweep
- Access:
  - `HasRequiredPermission` on every view, with the full role matrix tested
  - no new unauthenticated endpoint
- Data:
  - explicit serializer fields
  - audit entries carry IDs and the customer kind only (no names, phones or address text)
  - validation errors never echo input
- Concurrency: archive and update are serialized with `select_for_update`
- Dependencies: `pip-audit` clean

Not applicable: uploads, exports, OTP, public tracking, driver endpoints.

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

1. Owner reviews and merges `feature/api-uploads` (PR 1/4), then says "proceed" for PR 2 (`feature/api-fleet`).
2. The owner records the Phase 2 contracts and the Phase 3 PR 1 contract (upload flow, `uploads.create`, new error codes, `/health/ready` storage check; CHANGELOG entries) in fleetflow-admin and fleetflow-driver-app `memory.md`; owner-handled. The sibling repos still have no git remote.
3. Phase 3 PRs 2–4 as in phases.md. Architecture §7 (driver profile rule), §8 (fleet branch scope) and §10 (fleet model) are updated in the PR that builds them.
