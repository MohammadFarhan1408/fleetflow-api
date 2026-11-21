# fleetflow-api: Rules (non-negotiable)

These rules are enforceable. A change that breaks one fails review. To change a rule, follow §7.

## 1. General

- Read before writing: inspect the existing code, related tests and the relevant architecture section first.
- Reuse existing services, selectors, permissions, factories and error codes. Search for them (`rg`) before creating new ones.
- No speculative features, abstractions or "for later" scaffolding. An interface is allowed only when two real implementations exist.
- No premature optimization: measure first (query count, profiling).
- Keep changes focused. Don't modify unrelated files and don't reformat files you aren't otherwise changing.
- Don't remove working functionality without a stated reason and owner approval.
- Clear names, small focused functions, maintainable over clever.
- Handle failures explicitly. Never `except Exception: pass`. Catch narrow exceptions and log with context, then re-raise or map to a `DomainError`.
- Never hardcode secrets, URLs or credentials. Use env vars via settings and document each one in `.env.example`.
- No placeholder implementations, fake logic, hardcoded mock responses or unresolved TODOs in production code.
- Don't rewrite working code just to restyle it.

## 2. Backend-specific

- **Views are thin.** Business rules and writes live in `services.py`; reads go in `selectors.py`.
- **Tenant scoping is mandatory.** Every query on tenant data starts from the tenant-scoped manager. The company comes from `request.user`, never from the request body, query or headers.
- **Cross-tenant access returns 404**, not 403.
- **State changes go only through the transition services** (`shipments.services.transition`, route/stop services). No direct `status =` assignment outside them.
- **Append-only tables** (TrackingEvent, ScanEvent, AuditLog, LocationPing, POD) are never updated or deleted by application code.
- **Transactions**: multi-row writes use `transaction.atomic()`; contention-sensitive writes use `select_for_update()`; side effects go through `transaction.on_commit` or the outbox.
- **Celery tasks** take IDs, are idempotent, and retry only transient errors.
- **DB constraints** back every important invariant (FK, unique, check) in addition to serializer validation.
- **Migrations**: never edit an applied migration; keep schema and data migrations separate; large-table changes follow expand → backfill → contract; `makemigrations --check` must pass.
- **API contract**: every endpoint has a serializer and is in the OpenAPI schema. Changing a response shape or enum is a contract change (rule 6).
- No raw SQL without review and parameters. No `extra()`. Use `.only()`/`.defer()` and `select_related` to avoid N+1 (tests assert query counts on hot endpoints).
- Money uses `Decimal` (never float). Time is stored in UTC, timezone-aware. Coordinates are SRID 4326.
- **Module dependencies** follow the direction in architecture §5. No circular imports between apps; place code in the app that owns the domain, not in a generic `utils`/`helpers` module.
- **Serializers declare explicit fields.** Never `fields = "__all__"`; internal IDs and sensitive fields are never exposed by accident.
- **List endpoints are always paginated** (architecture §6) and fetch only the fields they return.
- **Indexes** are added for real query patterns (filters, ordering, FKs used in joins), not speculatively.
- **Duplicate requests and concurrency**: mutations that can be retried use idempotency keys (architecture §20); races are handled with locks or DB constraints, not hope.
- **Error categories stay distinct**: validation (400), authentication (401), authorization (403/404), business-rule or state conflicts (409/422), upstream failures (502/503) and unexpected errors (500). Each maps to a registered error code; messages are user-safe and never leak internals.

## 3. Security

- Validate and sanitize all input at the serializer. Treat clients, integrations and files as untrusted.
- Server-side permission check on every endpoint (DRF permission class) **and** in the service for object-level rules.
- Never log passwords, tokens, OTPs, API keys, signature images, full phone numbers or exact customer addresses. Mask them.
- OTPs are stored hashed, with attempt-limiting and resend-limiting.
- Uploads: presigned with size and type conditions, magic-byte verification, private bucket, short-lived read URLs.
- Rate-limit login, refresh, OTP, password reset, public tracking and ping ingestion.
- Public tracking exposes only whitelisted fields. Internal GPS history and driver PII are never public.
- Least privilege for roles and service credentials. Audit-log important operations (architecture §23).
- `pip-audit` must pass. Any accepted vulnerability is documented in memory.md known issues with a reason and expiry date.
- Guard specifically against: injection, XSS (in any rendered output, emails, exports), CSRF on any cookie-authenticated endpoint, broken access control and IDOR (object-level checks plus tenant scoping), SSRF (outbound URLs from webhooks or integrations are allow-listed and never reach internal addresses), path traversal (never build storage paths from user input), and sensitive-data exposure.
- No `eval`/`exec`, no `pickle` or other unsafe deserialization of untrusted data, no `shell=True` with user input.
- Secure defaults stay on: `DEBUG=False` outside local, strict security headers, `Secure`/`HttpOnly`/`SameSite` cookies. Never disable a security control to make a feature work.
- Clients never see stack traces, SQL, file paths or infrastructure details; they get the error envelope and a `request_id`.
- A security vulnerability found during development blocks the affected feature until it is fixed.

## 4. Testing

- Tests reflect business behavior; don't test framework internals.
- Every new endpoint ships with tests for 2xx, validation 400, 401, permission 403, cross-tenant 404, and 409 where state applies.
- Test failure paths, boundaries, state transitions, idempotency and concurrency where relevant (architecture §26.4).
- Don't delete or weaken tests or assertions to make a build pass. Don't `skip`/`xfail` security or permission tests.
- Mock only external HTTP and providers. Use real Postgres/PostGIS. Use factories, not fixtures of raw dicts.
- Run tests for the changed modules before reporting. Never claim tests passed without running them. State untested areas honestly.
- Writing code is not completion. Cover success, failure, validation, permission and boundary cases for the behavior you changed.

## 5. Code quality

- `ruff` (lint and format) and `mypy` clean. No new `# type: ignore` or `# noqa` without an inline reason.
- Files over ~500 lines or functions over ~50 lines are a signal to split along real responsibilities, not to invent layers.
- No duplicate logic: extract helpers only when reuse is real (2+ call sites).
- Typed contracts: type hints on all service and selector signatures; dataclasses or TypedDicts for service inputs where helpful.
- Avoid `Any`, `cast()` and untyped `dict` payloads. If one is unavoidable, isolate it and add a comment explaining why.
- Before writing a function, search for an existing one that does the same job. When fixing a bug, fix the shared logic, not each caller. Shared business rules have one source of truth.
- Meaningful names: no `data`, `item`, `temp`, `result2`, `handle_stuff` or `common_helper` when a precise name exists. Avoid abbreviations unless they are standard in the domain.
- Avoid deep nesting; prefer early returns and guard clauses.
- Comments explain _why_ (a decision, a constraint, a non-obvious invariant), never narrate what the code does.
- Remove dead code, unused imports, debug `print`s and obsolete logic in the code you touch.
- Don't create files, folders or layers just to look organized.

## 6. Contracts & cross-app

- fleetflow-api owns the API contract and enums. Any change to them: update the OpenAPI schema, add a changelog entry (`api-breaking` / `api-additive`), and record it in the memory.md of fleetflow-admin and/or fleetflow-driver-app.
- Breaking changes require a new version path or a deprecation window that covers the oldest supported driver-app build.

## 7. Architecture & dependency changes

Stop and present: the current decision, the problem, the proposed change, alternatives, pros and cons, dependency, testing and cross-app impact, and a migration plan. Get owner approval, then update architecture.md, these rules and memory.md. For dependencies, follow the checklist in CLAUDE.md §Dependencies.

## 8. Git

- Run `git status` and `git branch --show-current` before any change. Never develop on `main`.
- Branch prefixes: `feature/`, `fix/`, `refactor/`, `test/`, `docs/`, `chore/` (e.g. `feature/api-auth`, `fix/shipment-status-transition`).
- Conventional Commits (`feat(shipments): add transition endpoint`). Keep commits focused; no unrelated changes.
- No force push, no history rewriting, no amending unless asked, no merging without approval, no destructive commands (`reset --hard`, `clean -fd`, `branch -D`) without approval.
- Don't overwrite uncommitted user changes. Stash only with permission.
- Record branch work in memory.md §Branch history.
- Don't upgrade or downgrade unrelated dependencies; keep the lockfile in sync with every dependency change.
- Revert only your own changes, or others' with explicit approval. Review the full diff before declaring a task done.

## 9. Documentation

Update the relevant doc when: a dependency is added or removed (architecture §2 and memory), architecture or the API contract changes, a phase status changes, a rule changes, a major module lands, a test strategy changes. Never document unbuilt features as done or untested code as tested.

## 10. Constants & configuration

- No magic numbers or strings. A value repeated across files (status, role, permission identifier, error code, event or task name, cache key, header name, pagination default, validation limit) has one definition.
- Enums are `TextChoices`/`IntegerChoices` in the owning app's `models.py`; they are part of the API contract (§6).
- Error codes live in the `core/errors.py` registry; permission identifiers in the owning app's `permissions.py`; other shared values in the owning app's `constants.py`, created only when needed.
- Scope constants by domain. No single project-wide constants file.
- A value used once and clear inline stays inline. Don't create a constant for it.
- Environment-specific values (hosts, credentials, limits that differ per environment, feature flags) come from env vars read in `config/settings`, are type-converted and validated at startup, and fail fast when required ones are missing.
