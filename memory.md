# fleetflow-api: Memory (progress ledger)

Factual record only. Update it after every substantial task, dependency or architecture change, and branch merge. Never record unrun tests as passed. Keep entries short, and newest first within each section.

## Project status

| Field          | Value                                                                          |
| -------------- | ------------------------------------------------------------------------------ |
| Application    | fleetflow-api (backend)                                                        |
| Current phase  | Phase 0: Architecture & Project Setup (docs done; technical setup not started) |
| Current branch | `main` (repo initialized, **no commits yet**)                                  |
| Overall status | Documentation drafted, awaiting owner review. **No application code exists.**  |
| Last updated   | 2026-11-21                                                                     |

## Completed work

| Date       | Branch             | Work                                                                                        | Files                                                                                                                       | Tests run      | Result |
| ---------- | ------------------ | ------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------- | ------ |
| 2026-11-21 | main (uncommitted) | Workspace and documentation setup: repo initialized, docs written, Claude skills configured | CLAUDE.md, architecture.md, phases.md, rules.md, memory.md, skills.md, .claude/settings.json, .claude/skills/\*, .gitignore | None (no code) | n/a    |

## Current work

| Task                         | Branch             | Status                | In progress | Blockers                                  |
| ---------------------------- | ------------------ | --------------------- | ----------- | ----------------------------------------- |
| Phase 0 documentation review | main (uncommitted) | Awaiting owner review | —           | Owner decisions D-API-1, D-API-2, hosting |

## Decisions

| ID      | Decision                                                                                                                                                                | Context                                               | Alternatives                       | Reason                                                                                                                                                        | Date       | Branch | Status           | Impact                                    |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | ---------------- | ----------------------------------------- |
| D-WS-1  | Three independent git repos in one workspace folder, no root CLAUDE.md                                                                                                  | Owner instruction                                     | Monorepo                           | Independent release cycles and history                                                                                                                        | 2026-11-21 | main   | APPROVED (owner) | Cross-app changes = separate PRs per repo |
| D-WS-2  | Folder names fleetflow-api / fleetflow-admin / fleetflow-driver-app                                                                                                     | Owner choice                                          | backend/admin-panel/mobile-app     | Industry-standard naming                                                                                                                                      | 2026-11-21 | main   | APPROVED (owner) | All docs use these names                  |
| D-API-0 | Modular monolith (Django + DRF)                                                                                                                                         | architecture §3                                       | Microservices                      | Single team, transactional core                                                                                                                               | 2026-11-21 | main   | PROPOSED         | —                                         |
| D-API-1 | Django 5.2 LTS vs 6.1                                                                                                                                                   | simplejwt/django-storages classifiers stop at 5.2/5.1 | Django 6.1                         | Ecosystem compatibility, LTS until 2028-04                                                                                                                    | 2026-11-21 | main   | NEEDS APPROVAL   | Version pins in Phase 0                   |
| D-API-2 | Admin auth = JWT through the Next.js BFF in httpOnly cookies                                                                                                            | architecture §7                                       | Django session plus CSRF           | One auth path for both clients                                                                                                                                | 2026-11-21 | main   | NEEDS APPROVAL   | Affects fleetflow-admin                   |
| D-API-3 | Shared-schema tenancy with company_id                                                                                                                                   | architecture §9                                       | schema-per-tenant, DB-per-tenant   | Simplicity at MVP scale                                                                                                                                       | 2026-11-21 | main   | PROPOSED         | —                                         |
| D-API-4 | Realtime: Channels WS for admin only; driver uses REST plus push                                                                                                        | architecture §17                                      | WS for both, SSE, polling          | Mobile reliability and battery                                                                                                                                | 2026-11-21 | main   | PROPOSED         | Affects both clients                      |
| D-API-5 | OpenAPI (drf-spectacular) as the contract source; clients generate types                                                                                                | architecture §25                                      | Hand-written types, shared package | Single source of truth across 3 repos                                                                                                                         | 2026-11-21 | main   | PROPOSED         | Both clients                              |
| D-WS-3  | Engineering standards added: principles and definition of done in CLAUDE.md; enforceable detail merged into rules.md §1–5, §8 and new §10 (constants and configuration) | Owner-supplied engineering standards                  | Paste the full text into CLAUDE.md | Avoids duplicating existing rules and keeps CLAUDE.md a short entry point; frontend-only items (TypeScript, ESLint, UI styling) omitted for this backend repo | 2026-11-21 | main   | APPROVED (owner) | All future API work                       |

## Dependencies added/changed

None installed. Proposed stack: architecture.md §2.

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

- 2026-11-21: Repo initialized (`git init -b main`). Baseline documentation written (uncommitted, pending owner review and first commit).
- No application implementation.

### feature/api-setup (planned)

- Status: Not started. Planned work: Phase 0 technical setup. Implemented: none. Tests: none.

### feature/api-auth (planned)

- Status: Not started. Planned work: Phase 1 core foundation. Implemented: none. Tests: none.

## Known issues

| Issue                                                                                            | Severity | Branch | Status | Resolution                           |
| ------------------------------------------------------------------------------------------------ | -------- | ------ | ------ | ------------------------------------ |
| djangorestframework-simplejwt 5.5.1 and django-storages 1.14.6 do not declare Django 6.x support | Medium   | —      | Open   | Decide D-API-1; verify in Phase 0 CI |
| PostGIS image tag for PG18 not yet pinned                                                        | Low      | —      | Open   | Pin in Phase 0                       |

## Next steps

1. Owner reviews docs and decides D-API-1, D-API-2 and hosting target.
2. First commit of docs on `main` (owner action, or on request).
3. Create `feature/api-setup` and start Phase 0 technical setup.
