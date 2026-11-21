# fleetflow-api: Architecture

Status legend: **APPROVED** = owner-approved · **PROPOSED** = recommended, awaiting approval · **NEEDS APPROVAL** = open choice, owner must decide · **FUTURE** = not in MVP.

> The owner approved the Phase 0 docs on 2026-11-22. Items still marked PROPOSED are recommendations that have not been individually approved.
> Research date: **2026-11-21**. Versions were taken from the PyPI JSON API and package classifiers on that date. Re-verify at Phase 0 kickoff.

## Table of contents

1. [Responsibilities](#1-responsibilities)
2. [Technology](#2-technology)
3. [Architecture style](#3-architecture-style)
4. [Project structure](#4-project-structure)
5. [Module boundaries](#5-module-boundaries)
6. [API conventions](#6-api-conventions)
7. [Authentication & tokens](#7-authentication--tokens)
8. [Authorization (RBAC)](#8-authorization-rbac)
9. [Tenant isolation](#9-tenant-isolation)
10. [Data model](#10-data-model)
11. [Shipment lifecycle](#11-shipment-lifecycle)
12. [Tracking events](#12-tracking-events)
13. [Routes, stops & assignments](#13-routes-stops--assignments)
14. [Warehouse](#14-warehouse)
15. [Proof of delivery & exceptions](#15-proof-of-delivery--exceptions)
16. [Geospatial & GPS](#16-geospatial--gps)
17. [Real-time](#17-real-time)
18. [Background jobs & notifications](#18-background-jobs--notifications)
19. [File storage & uploads](#19-file-storage--uploads)
20. [Idempotency, concurrency, rate limiting](#20-idempotency-concurrency-rate-limiting)
21. [Errors, logging, observability](#21-errors-logging-observability)
22. [Performance & caching](#22-performance--caching)
23. [Security](#23-security)
24. [Environments, config, backup](#24-environments-config-backup)
25. [Cross-app contracts](#25-cross-app-contracts)
26. [Testing architecture](#26-testing-architecture)
27. [MVP vs future](#27-mvp-vs-future)

---

## 1. Responsibilities

fleetflow-api owns: authentication and authorization; companies (tenants), branches and users; roles and permissions; customers and addresses; shipments, shipment items and tracking events; drivers, vehicles and their documents; routes, route stops, dispatch and assignments; warehouse receiving, sorting, staging and loading; delivery exceptions; proof of delivery; GPS/location processing; maintenance and fuel; notifications; invoices and charges; reporting APIs; audit logs; and external integrations.

It is the **only** place where business rules and permissions are enforced. fleetflow-admin and fleetflow-driver-app are untrusted clients.

## 2. Technology

| Concern        | Choice                                          | Version (2026-11-21)             | Status         | Notes                                                                                                 |
| -------------- | ----------------------------------------------- | -------------------------------- | -------------- | ----------------------------------------------------------------------------------------------------- |
| Language       | Python                                          | 3.13                             | PROPOSED       | Supported by every library below. 3.14 is allowed once all dependencies list it                       |
| Framework      | Django                                          | **5.2 LTS** (5.2.19 installed)   | APPROVED       | See decision D-API-1                                                                                  |
| API            | Django REST Framework                           | 3.18.3                           | PROPOSED       | Classifiers cover Django 5.2–6.1                                                                      |
| OpenAPI        | drf-spectacular                                 | 0.30.0                           | PROPOSED       | Generates the OpenAPI 3 schema that is the contract for admin and driver-app                          |
| Auth tokens    | djangorestframework-simplejwt                   | 5.5.1                            | PROPOSED       | Classifiers stop at Django 5.2. Another reason for 5.2 LTS                                            |
| Filtering      | django-filter                                   | 26.2                             | PROPOSED       |                                                                                                       |
| DB             | PostgreSQL 18 + PostGIS 3.6                     | image `postgis/postgis:18-3.6`   | PROPOSED       | Plain `postgresql` engine until Phase 2 (memory D-API-8); the extension is enabled by a migration     |
| DB driver      | psycopg 3                                       | 3.3.9                            | PROPOSED       |                                                                                                       |
| Cache/broker   | Redis                                           | 7.x/8.x server; `redis` py 8.1.0 | PROPOSED       | Celery broker, cache, rate-limit counters, Channels layer                                             |
| Jobs           | Celery                                          | 5.7.0                            | PROPOSED       | Celery beat for schedules                                                                             |
| Realtime       | Django Channels + channels-redis                | 4.3.2 / 4.3.0                    | PROPOSED       | See §17                                                                                               |
| ASGI server    | uvicorn (behind gunicorn workers or standalone) | 0.54.0 / gunicorn 27.0.1         | PROPOSED       |                                                                                                       |
| Object storage | S3-compatible via django-storages               | 1.14.6                           | PROPOSED       | Added in Phase 3. Local S3-compatible server chosen then (MinIO images unmaintained, memory D-API-9) |
| Config         | django-environ                                  | 0.14.0                           | PROPOSED       |                                                                                                       |
| Logging        | structlog                                       | 26.1.0                           | PROPOSED       | JSON logs                                                                                             |
| Errors/APM     | sentry-sdk                                      | 2.75.0                           | PROPOSED       | Requires a vendor account. NEEDS APPROVAL for paid tier                                               |
| Lint/format    | ruff                                            | 0.17.6                           | PROPOSED       |                                                                                                       |
| Types          | mypy (+ django-stubs 5.2.x)                     | 1.19.1                           | PROPOSED       | Held below 2.x by django-stubs 5.2 `compatible-mypy`                                                  |
| Containers     | Docker + Compose                                | —                                | PROPOSED       | Development parity: api, worker, beat, postgres-postgis, redis (object storage from Phase 3)          |
| Packaging      | uv                                              | 0.12.1                           | PROPOSED       | `pyproject.toml` + `uv.lock`; also used in Dockerfile and CI (memory D-API-7)                         |

**D-API-1 (APPROVED 2026-11-22): Django 5.2 LTS.** Recommendation: 5.2 LTS (security support until April 2028). simplejwt and django-storages declare support only up to 5.2/5.1. DRF, Channels, pytest-django and django-filter all support 5.2. Alternative: 6.1, after confirming the simplejwt and storages CI matrices. Upgrading from 5.2 to 6.x later is a planned Phase 11 or post-MVP task.

## 3. Architecture style

**Modular monolith** (PROPOSED): one Django project and one deployable image, run as three process types (web/ASGI, Celery worker, Celery beat). Modules are Django apps with explicit public interfaces.

Why not microservices: one team, a shared transactional core (a shipment's status, stop and POD must change atomically), and no independent scaling need yet. A second service would add network failure modes with no benefit. Revisit only if GPS ingestion volume demands separate scaling (see §16, FUTURE).

```mermaid
flowchart LR
  Admin[fleetflow-admin<br/>Next.js BFF] -->|HTTPS REST /api/v1| API
  Driver[fleetflow-driver-app<br/>Expo] -->|HTTPS REST /api/v1| API
  Driver -->|location batches| API
  Admin -->|WSS| WS
  Public[Public tracking page] -->|token REST| API
  subgraph fleetflow-api
    API[Django + DRF<br/>ASGI]
    WS[Channels consumers]
    W[Celery worker]
    B[Celery beat]
  end
  API --> PG[(PostgreSQL + PostGIS)]
  W --> PG
  API --> R[(Redis)]
  WS --> R
  W --> R
  B --> R
  API --> S3[(Object storage)]
  W --> Ext[SMS / Email / Push / Maps providers]
```

## 4. Project structure

```text
fleetflow-api/
├── config/                 # settings/{base,local,test,production}.py, urls, asgi, wsgi, celery
├── apps/
│   ├── core/               # base models (TimeStamped, TenantScoped), pagination, errors, idempotency
│   ├── accounts/           # users, auth, roles, permissions
│   ├── tenancy/            # companies, branches, tenant context
│   ├── customers/          # customers, business accounts, addresses
│   ├── shipments/          # shipments, items, status machine, tracking events
│   ├── fleet/              # drivers, vehicles, documents, availability
│   ├── dispatch/           # routes, stops, assignments, manifests
│   ├── warehouse/          # warehouses, receiving, sorting, staging, loading scans
│   ├── delivery/           # attempts, POD, OTP, exceptions, returns
│   ├── tracking/           # GPS ingestion, last-known location, ETA, public tracking
│   ├── maintenance/        # maintenance, fuel, costs, alerts            (Phase 8)
│   ├── notifications/      # templates, outbox, providers                (Phase 9)
│   ├── integrations/       # webhooks, external APIs                     (Phase 9)
│   ├── billing/            # charges, invoices, COD reconciliation       (FUTURE)
│   ├── reporting/          # KPI queries, exports                        (Phase 10)
│   └── audit/              # audit log
├── tests/                  # cross-module integration and e2e API tests
└── docker/ compose.yaml pyproject.toml
```

Inside each app: `models.py`, `services.py` (writes and business rules), `selectors.py` (tenant-scoped reads), `serializers.py`, `views.py`, `permissions.py`, `urls.py`, `tasks.py`, `tests/`, `factories.py`. Create these files only when they are needed.

## 5. Module boundaries

- Views stay thin: parse, authorize, call a service or selector, serialize.
- **Services** own all writes, transactions and state transitions. Other apps call a module's services and selectors, never its models' `.save()` directly.
- Cross-module side effects use `transaction.on_commit` plus Celery tasks or the outbox (§18), not Django signals for business logic.
- Dependency direction: `core ← tenancy ← accounts ← (customers, fleet) ← shipments ← (dispatch, warehouse, delivery, tracking) ← (reporting, notifications, integrations)`. Cycles are a review failure.

## 6. API conventions

- **Versioning**: URL prefix `/api/v1/`. Breaking change → `/api/v2/` for that resource, with the old version kept for at least one driver-app release cycle (mobile clients update slowly).
- **Resources**: plural kebab-case nouns (`/shipments/`, `/route-stops/`). Actions that are state transitions are explicit sub-resources: `POST /shipments/{id}/transitions/` with `{ "to": "out_for_delivery", "reason": ... }`, not a raw `PATCH status`.
- **IDs**: UUIDv7 public IDs, so they are not enumerable. Integer primary keys stay internal. Human references use `FF-<branch>-<seq>` tracking numbers.
- **Pagination**: cursor pagination by default (`?cursor=`, `page_size` ≤ 100, default 25). Limit/offset only for admin tables that need page numbers (capped at 10k offset).
- **Filtering and sorting**: `?status=in_transit&status=assigned&created_after=…&ordering=-created_at`. Order only by whitelisted fields.
- **Field casing**: `snake_case` JSON. Timestamps are ISO-8601 UTC with `Z`. Money is a decimal string plus an ISO-4217 `currency`. Weights are in kg and dimensions in cm, both as decimal strings.
- **Errors** use one envelope, in the spirit of RFC 9457:

```json
{
  "type": "validation_error",
  "title": "Invalid input",
  "status": 400,
  "code": "shipment.invalid_weight",
  "detail": "Weight must be > 0 and ≤ 30000 kg.",
  "errors": { "weight_kg": ["must be greater than 0"] },
  "request_id": "…"
}
```

- **Documentation**: drf-spectacular serves `/api/schema/` and `/api/docs/` (staff-only outside local). The schema is committed and diffed in CI (§25).

## 7. Authentication & tokens

| Client               | Mechanism (PROPOSED)                                                                                                                                                                  |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| fleetflow-admin      | Login through the Next.js server (BFF). Access and refresh JWTs are stored in **httpOnly, Secure, SameSite=Lax cookies set by the Next.js server**. The browser never sees raw tokens |
| fleetflow-driver-app | JWT access token (15 min) plus refresh token (30 days, rotating), stored in `expo-secure-store`                                                                                       |
| Public tracking      | Opaque, unguessable tracking token per shipment (≥128-bit). Read-only, limited fields, revocable                                                                                      |
| Integrations         | Per-company API keys, hashed at rest and scoped (FUTURE, Phase 9)                                                                                                                     |

- Refresh rotation plus a blacklist (simplejwt `token_blacklist`). Reuse of a rotated refresh token revokes the whole token family.
- Logout blacklists the refresh token. Access tokens expire naturally (short TTL).
- Login is rejected for: a disabled user, a suspended company, or a driver without an active driver profile (for the driver app).
- Brute-force protection: per-IP and per-account login throttles, plus an exponential lockout after N failures (§20).
- Password reset sends a single-use, time-limited token by email. The response never reveals whether the account exists.
- **D-API-2 (APPROVED 2026-11-22)**: the admin uses JWT through the Next.js BFF in httpOnly cookies (consistent with mobile), so there is one auth code path in the API. Rejected alternative: Django session cookies with CSRF.

## 8. Authorization (RBAC)

- Roles are per company: `owner`, `admin`, `ops_manager`, `dispatcher`, `fleet_manager`, `warehouse_staff`, `finance`, `customer_service`, `driver`, `viewer`. Custom roles are FUTURE.
- Permissions are codenames like `shipments.create` or `dispatch.assign_driver`. Roles map to permission sets in the DB, seeded by a data migration.
- **Branch scope**: users can be limited to branches. Selectors filter by the user's branch set unless they have the `*.all_branches` permission.
- **Object rules** live in DRF permission classes plus service-level checks (defence in depth). For example, a driver can only touch stops on routes assigned to them.
- Every check is server-side. The client UI hides actions as a convenience only.

## 9. Tenant isolation

- **Strategy (PROPOSED)**: shared database, shared schema, with a `company_id` foreign key on every tenant-owned table. Alternatives: schema-per-tenant (django-tenants) is heavier on migrations and has low value at this scale; database-per-tenant is too expensive. Revisit if enterprise isolation is ever sold (FUTURE).
- `TenantScopedModel` base plus a `TenantQuerySet.for_company(company)`. Selectors **must** start from the tenant-scoped manager. The unscoped `.objects` is used only in migrations, admin tooling and tests.
- The tenant comes from the authenticated user's company, **never** from a request parameter or header.
- Cross-tenant ID guessing returns **404** (not 403), so record existence is not leaked.
- Uniqueness constraints include `company_id` (e.g. `UNIQUE(company_id, external_reference)`).
- PostgreSQL Row-Level Security as a second layer is FUTURE and needs measurement.
- A CI test runs every list and detail endpoint as a user of company B against data owned by company A (§26).

## 10. Data model

Core entities and relationships (MVP subset bolded):

```mermaid
erDiagram
  COMPANY ||--o{ BRANCH : has
  COMPANY ||--o{ USER : employs
  USER }o--o{ ROLE : "has (per company)"
  COMPANY ||--o{ CUSTOMER : serves
  CUSTOMER ||--o{ ADDRESS : has
  CUSTOMER ||--o{ SHIPMENT : sends
  SHIPMENT ||--|{ SHIPMENT_ITEM : contains
  SHIPMENT ||--o{ TRACKING_EVENT : logs
  SHIPMENT }o--|| ADDRESS : "pickup / delivery"
  DRIVER ||--|| USER : is
  DRIVER ||--o{ DRIVER_DOCUMENT : holds
  VEHICLE ||--o{ VEHICLE_DOCUMENT : holds
  ROUTE }o--o| DRIVER : "assigned"
  ROUTE }o--o| VEHICLE : "assigned"
  ROUTE ||--|{ ROUTE_STOP : sequences
  ROUTE_STOP }o--o{ SHIPMENT : "pickup/delivery of"
  ROUTE_STOP ||--o{ DELIVERY_ATTEMPT : records
  DELIVERY_ATTEMPT ||--o| PROOF_OF_DELIVERY : produces
  DELIVERY_ATTEMPT ||--o| DELIVERY_EXCEPTION : raises
  WAREHOUSE ||--o{ SCAN_EVENT : records
  DRIVER ||--o{ LOCATION_PING : emits
  VEHICLE ||--o{ MAINTENANCE_RECORD : has
  VEHICLE ||--o{ FUEL_RECORD : has
  COMPANY ||--o{ AUDIT_LOG : records
```

Principles:

- Every table has `id` (bigint, internal), `public_id` (UUIDv7) where it is exposed, `created_at` and `updated_at`. Tenant tables also have `company_id`.
- Use DB constraints over app-only checks: `CHECK (weight_kg > 0)`, unique constraints, FK `on_delete=PROTECT` for business records.
- **Soft delete** (`archived_at`) for master data: customers, addresses, drivers, vehicles, users (deactivate) and warehouses. Archived rows stay referenced by history. **Hard delete** is never used for shipments, tracking events, POD, scans, audit logs or financial records. Drafts are the exception: a shipment in `draft` can be hard-deleted, and the deletion is audited.
- `ShipmentItem` holds weight, dimensions, quantity and a barcode (one per parcel). The shipment's total weight is computed and cached.
- Addresses store normalized fields plus `location` (PostGIS `PointField`, SRID 4326) and a geocode-confidence field.
- **Migrations**: one migration per PR per app where possible. No edits to applied migrations. Data migrations are separate from schema migrations. Large tables follow expand → backfill (Celery, batched) → contract. CI runs `makemigrations --check`.

## 11. Shipment lifecycle

Statuses (canonical enum, owned here; see §25):

```mermaid
stateDiagram-v2
  [*] --> draft
  draft --> created : confirm
  draft --> [*] : delete (draft only)
  created --> received_at_warehouse : warehouse receive scan
  created --> assigned : direct pickup route
  received_at_warehouse --> sorted
  sorted --> assigned : added to route
  assigned --> loaded : loading scan verified
  loaded --> out_for_delivery : route dispatched/started
  assigned --> out_for_delivery : pickup-and-deliver route (no warehouse)
  out_for_delivery --> delivered : POD accepted
  out_for_delivery --> delivery_failed : exception recorded
  delivery_failed --> assigned : retry (reassign)
  delivery_failed --> returning : return to warehouse
  returning --> received_at_warehouse : return receive scan
  returning --> returned_to_sender : RTS completed
  created --> cancelled
  sorted --> cancelled
  assigned --> cancelled : removes stop
  delivery_failed --> cancelled
  cancelled --> created : recovery (ops_manager, audited)
  delivered --> [*]
  returned_to_sender --> [*]
```

Rules:

- Transitions happen **only** through `shipments.services.transition(shipment, to, actor, reason, idempotency_key)`. That function checks the transition table, the actor's permission and the preconditions (e.g. `loaded` requires a loading scan, `delivered` requires an accepted POD).
- `delivered` is terminal. Corrections use `admin_correction` (permission `shipments.correct_status`, mandatory reason, audited), which appends a correction event and never rewrites history.
- `cancelled → created` recovery is permitted only through the recovery workflow, never directly to `delivered`.
- Concurrency: `select_for_update()` on the shipment row inside the transaction, plus an `expected_status` precondition. On mismatch → 409 `shipment.status_conflict`.
- Repeating the same transition with the same idempotency key → returns the original result. Without a key, a transition to the current status → 409, so no duplicate event is created.

## 12. Tracking events

- `TrackingEvent` is append-only: `shipment`, `type` (status_change, scan, exception, note, correction, location_milestone), `status_after`, `occurred_at` (device time), `recorded_at` (server time), `actor`, `source` (admin, driver_app, warehouse, system, integration), `location` (optional point), `payload` (JSONB, schema per type), and `public` (bool: visible on customer tracking).
- `shipment.status` is a denormalized copy of the latest status_change event. It is written in the same transaction. A nightly Celery check flags mismatches (`status != last status_change.status_after`) to ops and the logs.
- The timeline is ordered by `occurred_at`, then `recorded_at`. Offline driver events can arrive late; `occurred_at` must not be in the future (with 5 min skew tolerance) and must not be earlier than the previous event minus the allowed drift. Out-of-order events are kept and flagged, never dropped.

## 13. Routes, stops & assignments

- `Route`: company, branch, date, status (`planned → assigned → loading → dispatched → in_progress → completed`; also `cancelled`), driver, vehicle, `sequence_enforced` (bool), and planned/actual distance and duration.
- `RouteStop`: route, `sequence`, type (`pickup|delivery|return`), address, time window, linked shipments, and status (`pending → arrived → completed | failed | skipped`). `UNIQUE(route_id, sequence)`, deferrable so stops can be reordered in one transaction.
- **Assignment rules** (service `dispatch.services.assign`): driver is active and not suspended, not on leave, has a valid unexpired license whose category covers the vehicle, and has no overlapping active route on that date; vehicle is active, not in maintenance or out of service, has unexpired registration and insurance documents, and has no overlapping route; total shipment weight and volume do not exceed vehicle capacity (hard block, with an `override_capacity` permission plus reason for soft overrides).
- Pickup-before-delivery dependency is validated when a route contains both for the same shipment.
- Reassigning during `in_progress` is allowed with `dispatch.reassign_active`. Completed stops stay with the original driver, and the remaining stops move. Both drivers get notified.
- **Optimization**: MVP uses manual sequencing plus a simple nearest-neighbour suggestion. An external optimizer (OSRM/VROOM self-hosted or a commercial API) is FUTURE and NEEDS APPROVAL. Its timeout or failure falls back to the current manual order, never blocking dispatch.

## 14. Warehouse

- Scan events (`receive`, `sort`, `stage`, `load`, `unload`, `return_receive`) are append-only, tied to warehouse, user, device and barcode.
- Receiving an unknown barcode → 404 code `scan.unknown_barcode` (no auto-create). Duplicate scan within a window → 200 with `duplicate: true` and no new state change.
- A shipment received at the wrong warehouse → accept the scan but flag `misrouted` and alert. Status does not advance.
- Loading verification: the route manifest is the expected set and loading scans are the actual set. A dispatch with a mismatch is blocked unless `dispatch.override_manifest` is used with a reason.
- Bulk CSV/XLSX import: max 10 MB / 5,000 rows (PROPOSED). Each row is validated and the import is all-or-nothing per batch by default; partial import is an explicit option that returns a row-level error report. Cells starting with `= + - @ \t \r` are escaped on **export** to prevent formula injection.

## 15. Proof of delivery & exceptions

- POD types per service type or customer policy: `otp`, `signature`, `photo`, `recipient_name`; a combination may be required.
- **OTP**: 6 digits, generated server-side when the shipment goes out for delivery, stored hashed, expires in 24 h (PROPOSED), max 5 attempts, then locked (dispatcher can reissue). Resends are limited to 3 per hour. OTP verification requires connectivity; offline OTP is not supported. The driver can fall back to signature/photo only if the policy allows it.
- Signature: PNG/SVG uploaded via the same upload flow as photos (§19).
- Geofence check: if the POD location is more than the configured radius (default 300 m) from the delivery point, the POD is accepted but flagged `location_mismatch` for review.
- Delivering twice → 409, idempotent if the same idempotency key is used.
- **Exceptions**: type enum (`recipient_absent`, `address_not_found`, `refused`, `damaged`, `access_denied`, `unsafe`, `vehicle_issue`, `other`) with a mandatory reason. `other` requires a note. Resolution is one of: `retry`, `reassign`, `return_to_warehouse` or `cancel`. Resolutions are serialized per exception (row lock), so the second conflicting resolution gets 409.

## 16. Geospatial & GPS

- PostGIS `geography(Point,4326)` for addresses, stops and pings, with GiST indexes. The GeoDjango engine (`django.contrib.gis.db.backends.postgis`, needs GDAL/GEOS) is switched on in Phase 2 with the first geometry field; until then the `core` migration only enables the extension.
- Queries: shipments or stops within a zone polygon (`ST_Covers`), nearest available driver (`ST_DWithin` plus ordering by distance on last-known location), and distance from a POD to the address.
- **Ingestion**: the driver app sends batched pings (`POST /api/v1/tracking/pings/`, ≤ 100 points per request) every ~15–30 s while on shift. Each ping has lat, lng, accuracy, speed, heading, `recorded_at` and battery. Validation: lat ∈ [-90, 90], lng ∈ [-180, 180]; reject accuracy > 500 m as an update to last-known location (store it but flag it); dedupe on `(driver, recorded_at)`; reject `recorded_at` more than 5 min in the future.
- `DriverLocation` (one row per driver: last known) is updated with a "newer recorded_at wins" rule. `LocationPing` is history, partitioned by month (FUTURE: TimescaleDB, if volume requires it).
- Freshness is computed by the API (`live` < 2 min, `stale` 2–15 min, `offline` > 15 min) and is a shared contract (§25).
- **Retention (PROPOSED)**: raw pings are kept for 90 days, then downsampled. Location is only collected while a shift is active. Public tracking shows the driver's location only while the shipment is `out_for_delivery` and the shipment is the next stop (policy NEEDS APPROVAL).
- **ETA**: MVP is the haversine distance multiplied by an average speed factor per zone. A routing-engine ETA is FUTURE.

## 17. Real-time

- **Choice (PROPOSED)**: Django Channels over WebSocket, with the Redis channel layer, for the admin console only (dispatch board, live map, exception alerts).
- The driver app uses REST plus push notifications (§18), not a persistent socket. That is more reliable on mobile networks and better for battery.
- Auth: the WebSocket handshake uses a short-lived ticket (`POST /api/v1/realtime/tickets/` → a 30 s single-use token), because browsers can't set headers on WS and admin tokens live in httpOnly cookies.
- Groups: `company:{id}:branch:{id}:dispatch`, `company:{id}:tracking`. Server membership checks run on subscribe.
- Events are **notifications to refetch**, not the source of truth: `{type: "shipment.status_changed", id, status, version, occurred_at}`. Clients invalidate TanStack Query caches. A missed event is harmless because the client refetches on reconnect.
- Event names are part of the contract (§25).
- Fallback: if WS is unavailable, the admin polls every 15–30 s.

## 18. Background jobs & notifications

- Celery queues: `default`, `notifications`, `imports`, `reports`, `tracking`. Beat runs scheduled jobs (document expiry, maintenance-due alerts, status/event consistency check, ping retention).
- Every task is **idempotent**: tasks receive IDs, not objects, re-read state, and check whether the work is already done. Use `acks_late=True` plus `reject_on_worker_lost` for critical tasks. Retries use exponential backoff with jitter and a max of 5 attempts, and are only triggered by transient errors.
- **Transactional outbox** for notifications and webhooks: write `OutboxMessage` in the same DB transaction as the business change; a dispatcher task publishes it. This keeps a rollback from sending a phantom SMS, and a broker outage loses nothing.
- Failed after max retries → `status=failed` (dead-letter table), visible in admin, can be retried manually.
- Notification providers (email, SMS, push via Expo Push Service or FCM/APNs) sit behind a small provider interface **only because more than one provider exists**. Templates have required-variable validation at save time; a missing variable at send time → fail the message, not send a broken one.
- All schedule times are stored in UTC. Business-day logic uses the company's timezone (`Company.timezone`).

## 19. File storage & uploads

- Private bucket. Objects are keyed `company/{id}/pod/{yyyy}/{mm}/{uuid}.{ext}`. They are never public; reads use short-lived (5 min) presigned GET URLs after a permission check.
- Upload flow: client requests `POST /uploads/` (purpose, content type, size) → API returns a presigned PUT/POST with content-length and content-type conditions → client uploads → client calls `confirm` → API verifies the object size and type (magic bytes), strips EXIF location from photos (PROPOSED: keep the original in a restricted bucket when evidence is needed, NEEDS APPROVAL), and links it to the POD.
- Allowed: JPEG, PNG, HEIC→JPEG (converted on device), PDF for documents. Max 10 MB per photo, 20 MB per document. Malware scanning (ClamAV sidecar) is FUTURE, and Phase 11 NEEDS APPROVAL.
- Orphaned uploads (never confirmed) are purged after 24 h.

## 20. Idempotency, concurrency, rate limiting

- `Idempotency-Key` header is **required** on all driver-app mutations and on shipment creation; it is optional elsewhere. Stored `(company, user, key) → request hash, response, status` for 72 h. Same key plus a different payload → 422 `idempotency.key_reuse`.
- Concurrency: `select_for_update` for state transitions and assignments, plus optimistic `version` integers on routes and stops (409 on stale version). This is what the dispatch board uses for concurrent edits.
- Rate limits (DRF throttles backed by Redis, PROPOSED values): login 5/min per IP+username, OTP verify 5/shipment total plus 10/min per driver, OTP resend 3/h, pings 6/min per driver, public tracking 60/min per IP, general authenticated 600/min per user.

## 21. Errors, logging, observability

- One exception handler maps domain errors (`DomainError(code, detail, status)`) to the error envelope (§6). Unexpected errors → 500 with `request_id` only.
- Logs are structlog JSON with `request_id`, `company_id`, `user_id` and `route`. **Never** log passwords, tokens, OTPs, full phone numbers, signatures or precise customer addresses at info level (mask them).
- `/health/live` (process up) and `/health/ready` (DB, Redis, storage reachable). These are used by Docker and the orchestrator.
- Metrics (FUTURE, Phase 11): request latency per endpoint, Celery queue depth, task failures, ping ingest rate. Sentry for errors (NEEDS APPROVAL on the vendor).

## 22. Performance & caching

- Every list selector uses `select_related`/`prefetch_related`. CI includes `assertNumQueries`-style tests for hot endpoints (shipment list, dispatch board, route detail).
- Indexes: `(company_id, status, created_at)` on shipments, `(company_id, route_date)` on routes, GiST on geometry, `(driver_id, recorded_at)` on pings.
- Cache only what is measured to be slow. Candidates: dashboard KPI aggregates (60 s TTL keyed by company and filters) and reference data (service types, zones). Never cache permission decisions across requests.
- Targets (PROPOSED, for Phase 11 load tests): p95 < 300 ms for list endpoints with 100k shipments per company, and sustained ping ingest of 500 drivers × 1 batch/30 s.

## 23. Security

OWASP ASVS L2 as the baseline. Specifics: tenant scoping (§9); server-side RBAC (§8); validation in serializers and services; parameterized ORM only, with any raw SQL reviewed; CORS allow-list (the admin BFF origin; mobile has no CORS); strict security headers; `DEBUG=False` outside local; Argon2 password hashing; secrets only via environment or a secret manager; upload hardening (§19); rate limits (§20); audit log (below); dependency audit in CI (`pip-audit`); no PII in logs.

**Audit log**: append-only `AuditLog(company, actor, action, object_type, object_id, before, after, ip, user_agent, request_id, at)`, written by services for logins, permission changes, status corrections, assignment changes, POD overrides, exports, settings changes and deletions. Readable only with `audit.view`.

## 24. Environments, config, backup

- Environments: `local` (Docker Compose), `test` (CI), `staging`, `production`. Settings modules per environment. All secrets and endpoints come from env vars, and `.env.example` documents each one.
- Backups (PROPOSED, Phase 11): managed PostgreSQL with point-in-time recovery (7–30 days), daily logical dump to separate storage, object storage versioning on the POD bucket, and a quarterly restore drill. RPO ≤ 15 min, RTO ≤ 4 h (NEEDS APPROVAL).
- Hosting target: deferred to Phase 11 (owner decision 2026-11-22, memory D-API-6). Candidates: AWS ECS/RDS, Render or Fly.

## 25. Cross-app contracts

fleetflow-api **owns** every contract. Admin and driver-app must not invent fields, statuses or error codes.

| Contract                                                                                            | Source of truth                                                                       |
| --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| REST resources and payloads                                                                         | OpenAPI schema generated by drf-spectacular, committed as `openapi/schema.yaml`       |
| Enums: shipment status, route status, stop status, exception types, POD types, roles, GPS freshness | Python `TextChoices` → exported in the OpenAPI schema                                 |
| Error envelope and error codes                                                                      | §6 plus the `core/errors.py` registry                                                 |
| Realtime event names and payloads                                                                   | `realtime/events.py` registry plus a documented table in this file (added in Phase 7) |
| Upload flow                                                                                         | §19                                                                                   |
| Idempotency semantics                                                                               | §20                                                                                   |

**Strategy (PROPOSED, implement in Phase 1 of the clients):** CI on fleetflow-api fails if `schema.yaml` changes without a `CHANGELOG` entry tagged `api-breaking` or `api-additive`. Each client generates typed clients and types from the published schema (e.g. `openapi-typescript` plus a thin fetch wrapper, or `orval` with TanStack Query hooks, NEEDS APPROVAL at admin Phase 0) and pins the schema version it was built against. Every contract change is recorded in this file's changelog section and in the affected app's `memory.md`.

## 26. Testing architecture

### 26.1 Stack (PROPOSED; versions as of 2026-11-21)

| Tool                                       | Version    | Use                                                                  |
| ------------------------------------------ | ---------- | -------------------------------------------------------------------- |
| pytest                                     | 9.2.0      | runner                                                               |
| pytest-django                              | 4.14.0     | Django integration, `db` fixtures, `django_assert_num_queries`       |
| DRF `APIClient`                            | (DRF)      | endpoint tests                                                       |
| factory_boy                                | 3.3.3      | model factories, one per model, tenant-aware (`company` subfactory)  |
| Faker                                      | 40.63.0    | realistic data via factories                                         |
| coverage.py (via pytest-cov)               | 7.17.4     | branch coverage                                                      |
| pytest-xdist                               | 3.8.0      | parallel CI                                                          |
| hypothesis                                 | 6.170.17   | property tests for the state machine, coordinates and money rounding |
| `unittest.mock` / `responses` or `respx`   | —          | external HTTP providers only                                         |
| channels testing (`WebsocketCommunicator`) | (Channels) | WebSocket consumers                                                  |
| locust                                     | 2.47.0     | performance tests for critical endpoints (Phase 11)                  |

**Test database**: real PostgreSQL + PostGIS in Docker (same image as production). **Never SQLite**, because geospatial, `select_for_update` and constraint behavior differ. Tests use transactions; concurrency tests use `transaction=True` plus threads. Celery runs eagerly (`task_always_eager`) for unit and service tests. Separate integration tests run a real worker against Redis. External providers are always mocked at the HTTP boundary; never call real SMS or email in tests.

### 26.2 Test levels

| Level            | Scope                                        | Example                                                                  |
| ---------------- | -------------------------------------------- | ------------------------------------------------------------------------ |
| Unit             | pure functions                               | weight/volume calc, OTP hashing, ETA math, CSV escaping                  |
| Model            | constraints, managers                        | `CHECK weight>0`, `UNIQUE(company, external_reference)`, tenant manager  |
| Serializer       | validation                                   | phone/postal formats, cross-field (scheduled date vs service type)       |
| Service          | business rules and transactions              | `transition()`, `assign()`, `verify_otp()`                               |
| API              | HTTP contract, status codes, envelope        | every endpoint: 2xx, 400, 401, 403/404, 409                              |
| Permission       | role × action matrix                         | parametrized over roles                                                  |
| Tenant isolation | cross-company                                | automated sweep of all routes (below)                                    |
| Integration      | multi-module flows plus a real Celery worker | create → assign → load → deliver                                         |
| Background task  | idempotency, retries                         | run the task twice → one side effect                                     |
| WebSocket        | auth, group isolation                        | company B cannot join company A's group                                  |
| Geospatial       | PostGIS queries                              | `ST_DWithin` boundary, antimeridian, invalid SRID                        |
| Security         | auth, rate limits, uploads                   | brute force → 429, malicious file rejected                               |
| Performance      | p95 and query counts                         | `django_assert_max_num_queries(N)` on list endpoints; locust in Phase 11 |

**Tenant isolation sweep (mandatory)**: a test enumerates every URL pattern under `/api/v1/` and, using fixtures for company A's objects, calls each detail/update/delete route as a company B admin. It asserts 404 and no data in the body. A new endpoint without a fixture fails the test. That is deliberate.

### 26.3 Coverage policy (PROPOSED)

| Area                                                                                        | Target                                                                                       | Enforcement                                     |
| ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `services.py`, state machines, permissions, tenancy, idempotency, OTP/POD, assignment rules | **≥ 95 % branch**                                                                            | per-path `--cov-fail-under` in CI               |
| Views, serializers, selectors                                                               | ≥ 85 % line                                                                                  | CI                                              |
| Overall                                                                                     | ≥ 85 % line (floor, not a goal)                                                              | CI                                              |
| Excluded                                                                                    | migrations, `settings/`, `asgi.py`/`wsgi.py`, `manage.py`, admin.py registrations (no logic) | `.coveragerc` with a comment for each exclusion |

Merge gate: ruff + mypy + `makemigrations --check` + full pytest (unit, service, API, permission, tenant sweep) + OpenAPI diff check must pass. Integration tests with a real worker run on every PR to `main`. Locust runs nightly or before release, not per PR. A security or permission test can never be skipped or `xfail`ed without an entry in `memory.md` known issues.

### 26.4 Required scenarios by module

Each row is a test case with its expected outcome. Implement per module in that module's phase. Codes are error `code` values.

**Authentication & authorization**
| Scenario | Expected |
|---|---|
| Valid credentials | 200, access + refresh, `last_login` updated, audit `auth.login` |
| Wrong password / unknown user | 401 `auth.invalid_credentials`, same message and similar timing for both |
| Disabled user | 401 `auth.user_disabled`, no tokens |
| Suspended company | 403 `auth.company_suspended` at login; existing tokens rejected on next request |
| Expired access token | 401 `auth.token_expired` |
| Malformed or tampered token | 401 `auth.token_invalid` |
| Refresh valid | new pair; old refresh blacklisted |
| Refresh reused after rotation | 401, whole token family revoked, audit `auth.refresh_reuse` |
| Logout | refresh blacklisted; subsequent refresh 401 |
| Dispatcher calls `users.manage` endpoint | 403 `permission.denied` |
| Driver calls admin-only endpoint | 403 |
| Company B user fetches company A shipment by UUID | 404 |
| Company B user sends A's `customer_id` in a create payload | 400 `validation.invalid_reference` (not found within tenant) |
| 6th login failure within 1 min | 429 with `Retry-After` |
| Lockout after N failures across IPs | 423/429; unlock after window; audit |

**Shipment creation**
| Scenario | Expected |
|---|---|
| Valid shipment with 2 items | 201, status `created` (or `draft` if requested), tracking number issued, `TrackingEvent(created)` |
| Missing sender or recipient | 400, field errors |
| Address without geocode / invalid address | 400 `address.unresolvable`, or 201 with `geocode_confidence=low` per policy (NEEDS APPROVAL) |
| Invalid phone (E.164) / postal code per country | 400 field error |
| Weight 0 / negative / > 30,000 kg | 400 `shipment.invalid_weight` |
| Dimension 0 or > max per service type | 400 |
| Unsupported shipment or service type | 400 `shipment.unsupported_type` |
| Invalid priority value | 400 |
| Scheduled date in past / invalid format | 400 `shipment.past_schedule` |
| COD amount negative, wrong precision or currency mismatch | 400 |
| Duplicate `external_reference` in same company | 409 `shipment.duplicate_reference`; same ref in another company → allowed |
| Retry with same Idempotency-Key | original 201 response replayed, one shipment in DB |
| Same key, different body | 422 `idempotency.key_reuse` |
| Inactive or archived customer | 400 `customer.inactive` |
| User creates for another company's customer | 400 invalid reference (no leak) |
| Webhook/integration failure after create | shipment persists; outbox message retried; no 500 to client |
| 0 items / 500 items (boundary) | 400 / 201 within limit, 400 above limit |

**Shipment state transitions**
| Scenario | Expected |
|---|---|
| Every allowed edge in the §11 diagram | 200, event appended, `status` updated atomically |
| Every disallowed edge (generated from the table) | 409 `shipment.invalid_transition`, no event |
| `delivered → out_for_delivery` | 409 |
| `cancelled → delivered` | 409; only recovery to `created` with permission |
| Same transition repeated with same key | idempotent replay |
| Same transition repeated without key | 409, no duplicate event |
| Two concurrent transitions (threads) | exactly one succeeds, the other 409 `status_conflict` |
| Admin correction with reason | correction event appended, audit entry, history unchanged |
| Admin correction without permission or reason | 403 / 400 |
| Timeline order with late offline events | ordered by `occurred_at`; flagged out-of-order |
| Hypothesis: random valid event sequences | `status` always equals last status event |
| Consistency job finds mismatch | alert logged and recorded; no auto-fix |

**Driver & vehicle assignment**
| Scenario | Expected |
|---|---|
| Available driver + vehicle, capacity ok | 200, route `assigned`, both notified (outbox) |
| Driver unavailable / on leave / suspended | 409 `assignment.driver_unavailable` with reason subcode |
| Driver license expired (boundary: expires today, yesterday) | expired → 409; expires today → allowed (PROPOSED rule) |
| Driver lacks vehicle category | 409 `assignment.license_category` |
| Vehicle in maintenance / out of service | 409 `assignment.vehicle_unavailable` |
| Vehicle registration or insurance expired | 409 |
| Load exceeds weight or volume capacity | 409 `assignment.over_capacity`; with override permission + reason → 200 and audit |
| Same driver+route assigned twice | idempotent 200 |
| Driver already on overlapping route | 409 `assignment.driver_conflict` |
| Vehicle on overlapping route | 409 `assignment.vehicle_conflict` |
| Two dispatchers assign same driver concurrently to two routes | one 200, one 409 |
| Unassign before dispatch | 200, route back to `planned` |
| Reassign during `in_progress` | completed stops stay; pending move; both drivers notified |
| Dispatcher from another branch without all-branch permission | 404/403 |

**Dispatch & route operations**
| Scenario | Expected |
|---|---|
| Dispatch empty route | 409 `route.empty` |
| Single stop / 200 stops (boundary) | 200; above max (PROPOSED 300) → 400 |
| Reorder with duplicate or gapped sequence | 400 `route.invalid_sequence` |
| Same shipment twice as delivery on one route | 400 `route.duplicate_stop` |
| Delivery stop before its pickup | 400 `route.pickup_dependency` |
| Capacity exceeded after adding stop | 409 |
| Stop outside time window (planned ETA) | 200 with warning `time_window_risk` (warning, not block) |
| Stop with invalid coordinates or ungeocoded address | 400 |
| Optimizer timeout / unavailable | 200, manual order kept, `optimization: "unavailable"` |
| Cancel route with completed stops | 409; without → 200, shipments back to `sorted` |
| Driver starts route before dispatch | 409 `route.not_dispatched` |
| Stop completed out of sequence when enforced | 409 `stop.out_of_sequence`; not enforced → 200 |
| Dispatch with shipment not loaded | 409 `manifest.not_loaded` unless override |
| Wrong shipment scanned at loading | 409 `scan.not_on_manifest`, alert |
| Duplicate loading scan | 200 `duplicate: true`, no new event |
| Manifest reconciliation | report lists missing / extra / duplicate items exactly |
| Concurrent edit with stale `version` | 409 `route.version_conflict` |

**Tracking & GPS**
| Scenario | Expected |
|---|---|
| Valid batch of 20 pings | 202; last-known updated to newest |
| lat 91 / lng -181 / NaN / string | 400 per point; valid points in same batch still accepted (partial, per-point result) |
| Accuracy 800 m | stored, flagged, last-known not updated |
| Ping older than last-known | stored to history, last-known unchanged |
| `recorded_at` 10 min in future | rejected |
| Duplicate ping (same driver + time) | ignored, counted as duplicate |
| Burst of 1,000 pings in 1 min | 429 after limit; no DB overload (batch insert) |
| Driver offline > 15 min | freshness `offline` in API and events |
| Driver without active shift sends pings | 409 `tracking.no_active_shift`, not stored |
| WS disconnect then reconnect | client gets fresh ticket; no events replayed (refetch contract) |
| Dispatcher of company B subscribes to company A group | connection closed 4403 |
| Public token valid | 200 with whitelisted fields only (no driver phone, no exact driver location unless policy allows) |
| Public token invalid / revoked / expired | 404 |
| Public token used to access other shipment IDs | impossible by design; test confirms 404 |
| Retention job | pings older than 90 d removed or downsampled; last-known intact |

**Proof of delivery**
| Scenario | Expected |
|---|---|
| Correct OTP | 200, attempt success, shipment `delivered` |
| Wrong OTP | 400 `pod.otp_invalid`, attempts remaining returned |
| Expired OTP | 400 `pod.otp_expired` |
| 6th attempt | 423 `pod.otp_locked`; dispatcher reissue required |
| Resend 4th time in an hour | 429 |
| Signature upload valid | 200, linked |
| Photo JPEG 3 MB | 200 |
| `.exe` renamed `.jpg` (magic-bytes mismatch) | 400 `upload.type_mismatch`, object deleted |
| 25 MB photo | rejected at presign (size condition) and at confirm |
| Policy requires OTP + photo, photo missing | 400 `pod.missing_required_proof` |
| Deliver already-delivered shipment | 409 (or replay with same key) |
| Upload confirm fails (object missing) | 400 `upload.not_found`; POD not accepted; retryable |
| Offline POD synced later with original `occurred_at` | accepted; timeline ordered correctly |
| Same offline POD synced twice | one POD (idempotency key) |
| Recipient name missing when required | 400 |
| POD location 2 km from address | 200, flagged `location_mismatch` |
| Driver not assigned to the stop submits POD | 404 |

**Delivery exceptions**
| Scenario | Expected |
|---|---|
| Valid exception with reason | 201, shipment `delivery_failed`, event, dispatcher alert |
| Missing reason / `other` without note | 400 |
| Invalid type | 400 |
| Second exception same attempt | 409 or idempotent replay |
| Resolve: retry / reassign / return / cancel | correct transitions per §11 |
| Unresolved exception > SLA (PROPOSED 2 h) | alert job fires once (no duplicates) |
| Notification provider fails | exception still saved; outbox retries |
| Driver tries to close exception | 403 |
| Two dispatchers resolve concurrently | one 200, one 409 |

**Warehouse** (receiving, scans, imports)
| Scenario | Expected |
|---|---|
| Receive known shipment | 200, `received_at_warehouse` |
| Unknown barcode | 404 `scan.unknown_barcode` |
| Duplicate barcode across shipments (data issue) | 409 `scan.ambiguous_barcode`, alert |
| Already received | 200 `duplicate: true` |
| Wrong warehouse | 200 flagged `misrouted`, status unchanged |
| Shipment assigned to another route at loading | 409 `scan.wrong_route` |
| Bulk scan of 200 with 3 bad | per-item results; 197 applied |
| Valid CSV 5,000 rows | 202, import job, report |
| Missing headers / wrong encoding / XLSX with macros | 400 before processing |
| Partial import mode with invalid rows | valid rows created; row-level error report downloadable |
| Cell `=HYPERLINK(...)` in export | exported as `'=HYPERLINK(...)` |
| 11 MB file | 413 |

**Maintenance & fuel**
| Scenario | Expected |
|---|---|
| Valid maintenance record | 201; vehicle blocked if type `in_shop` |
| Odometer lower than last reading | 400 `vehicle.odometer_regression` (override with reason) |
| Negative cost / bad date | 400 |
| Duplicate fuel record (same vehicle, time, amount) | 409 or warning (PROPOSED: 409 within 10 min) |
| Consumption > 2× vehicle average | saved, flagged anomaly |
| Service due at exactly threshold km/date | alert fires once at boundary |
| Document expires; alert job runs twice | one alert |
| Assign vehicle blocked by maintenance | 409 (see assignment) |
| Cost totals with 3-decimal currencies and rounding | `Decimal` with banker's rounding PROPOSED; tested with fixtures |

**Notifications & background jobs**
| Scenario | Expected |
|---|---|
| Send succeeds | outbox `sent`, provider id stored |
| Provider 5xx / timeout | retried with backoff; `sent` on later success |
| Retries exhausted | `failed`, visible in admin, alert |
| Same event processed twice | one message (dedupe key) |
| Invalid phone or email | `failed` permanently, no retries |
| Template missing / variable missing | `failed` with `template.*` code; nothing sent |
| Scheduled job across DST change in company timezone | runs once at intended local time |
| Job cancelled before run | not executed |
| Worker down | outbox accumulates; drains on recovery; nothing lost |
| Business transaction rolls back | no outbox message, nothing sent |

**Reports & analytics**
| Scenario | Expected |
|---|---|
| Empty dataset | zeros and empty series, no 500 |
| 1M shipments | response within target using aggregates; export async |
| Date range boundaries (inclusive start, exclusive end, company TZ) | exact counts on fixtures spanning midnight and DST |
| Missing cost data | excluded and reported as `n/a`, not 0 |
| Cancelled shipments | excluded from delivery-rate denominator (documented KPI definition) |
| Delivery rate with 0 eligible | `null`, not division error |
| Invalid filter combo | 400 |
| Company B requests report filtered by A's branch | 404 / empty |
| Export without `reports.export` | 403 |
| CSV/XLSX export | values match API totals; formula-escaped |
| Paginated sum vs total | equal |

## 27. MVP vs future

**MVP (Phases 0–7)**: auth/RBAC/tenancy, customers, shipments plus lifecycle, fleet, dispatch/routes, delivery/POD/exceptions, warehouse scans, GPS ingestion with last-known location, admin realtime, basic notifications (email plus push).
**FUTURE**: route optimizer, routing-engine ETA, billing and invoices, customer portal accounts, integrations/webhooks marketplace, multi-tenant SaaS self-signup, RLS, malware scanning, TimescaleDB, schema-per-tenant, read replicas.
