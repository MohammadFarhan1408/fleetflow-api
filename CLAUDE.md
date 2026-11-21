# fleetflow-api: Claude Code Instructions

> **SETUP STAGE. NO IMPLEMENTATION.** Phase 0 (documentation and workspace setup) is in progress and awaiting owner review. Do not initialize Django, install packages, or create source files, models, APIs or tests until the owner approves Phase 0 and explicitly starts work. Documentation edits are allowed.

## Identity

|              |                                                                                                                                                                         |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| App          | **fleetflow-api**: the FleetFlow backend (Django + DRF modular monolith, PROPOSED)                                                                                      |
| Purpose      | System of record and the only security boundary for FleetFlow: auth, tenants, shipments, fleet, dispatch, warehouse, delivery/POD, GPS, notifications, reporting, audit |
| Boundaries   | Owns all data, business rules, state transitions, permissions and API contracts. Contains no UI.                                                                        |
| Related apps | `../fleetflow-admin` (ops web console) and `../fleetflow-driver-app` (driver mobile app). Both are API **consumers**.                                                   |
| Repo         | Its own git repo (`main`). Sibling apps are separate repos.                                                                                                             |

FleetFlow is three independent repos in one workspace folder. There is no root CLAUDE.md. Each app's CLAUDE.md is its entry point.

## Read before working (targeted, not cover-to-cover)

| Order | File                               | When                                                          |
| ----- | ---------------------------------- | ------------------------------------------------------------- |
| 1     | `CLAUDE.md` (this)                 | Always (auto-loaded)                                          |
| 2     | [rules.md](rules.md)               | Always. Skim headings; read the sections relevant to the task |
| 3     | [phases.md](phases.md)             | Confirm the task belongs to the current phase                 |
| 4     | [memory.md](memory.md)             | Current status, decisions, branch history                     |
| 5     | [architecture.md](architecture.md) | Only the sections the task touches (use the TOC)              |
| 6     | [skills.md](skills.md)             | **Every session**: pick the skill(s) for the task type        |

Trivial tasks (typo, one-line doc fix) need only this file and the relevant rule.

**Cross-app work** (API contract, status enum, auth flow, POD/upload contract, realtime events): also read the affected sibling's `CLAUDE.md`, the relevant `architecture.md` sections and `memory.md`. API changes that affect consumers must be recorded in this repo's architecture §Cross-app contracts and in each affected app's `memory.md`.

## Engineering standards

Work as a senior engineer, not a code generator. Correctness, security, maintainability and readability come before speed of delivery. The enforceable detail is in [rules.md](rules.md); these are the principles behind it.

- **Verify, don't assume.** If the codebase or docs can answer a question, inspect them instead of guessing. Base decisions on the actual requirements and the existing architecture.
- **Production-ready by default.** No placeholder logic, fake or hardcoded mock responses, invented features or integrations, or unresolved TODOs in production code unless the owner asks for a prototype.
- **Simple over clever.** No overengineering, premature abstraction or speculative layers. Explicit, readable code beats compact, clever code.
- **Challenge flawed approaches.** If a request or design is technically unsound, risky or unnecessary, say why and propose a better option instead of implementing it silently. Do not agree just to agree.
- **Unclear requirements.** Never invent requirements that affect architecture, security, data integrity or API contracts. If a safe, reasonable interpretation exists, state the assumption and proceed. If interpretations would lead to materially different implementations, ask concise questions first. Name the trade-offs when several solutions exist.
- **Fit the codebase.** Every change should have a clear purpose, follow existing patterns and conventions, and not rewrite working code just to make it look different.
- **Long-term view.** Write code a skilled engineer would be comfortable reviewing, securing and extending two years from now.

## Workflow (mandatory)

Do not skip investigation or verification to get to code faster.

1. Understand the request; ask if it is ambiguous in a way that changes the result.
2. Run `git status` and `git branch --show-current`. Never develop features on `main`.
3. Read the relevant docs (table above).
4. Confirm the phase allows the task. If it is out of phase, stop and say so.
5. List impacted modules and files, and any sibling apps affected.
6. Search for existing services, selectors, permissions and factories before writing new ones.
7. Medium or large change: present a plan first and wait for approval.
8. Implement the smallest complete solution.
9. Write or update tests (failure paths, permissions, tenant isolation, state transitions).
10. Run the relevant tests, `ruff`, `mypy`, and migration checks.
11. Review your own diff: unrelated changes, regressions, security, N+1 queries, missing tenant scoping, duplicated logic, hardcoded repeated values, and unnecessary files, abstractions or dependencies.
12. Update docs and `memory.md` (actual results only).
13. Report: what changed, which checks ran and their results, issues found, limitations, anything left unverified or incomplete, and next steps.

## Definition of done

A task is complete only when every item holds. If one cannot be met, say so in the report instead of declaring the task done.

- [ ] The requirements are met, and nothing beyond them was invented.
- [ ] The change follows the established architecture, module boundaries and folder structure.
- [ ] Existing behavior and API contracts are not broken (or the break is approved and recorded per rules.md §6).
- [ ] No duplicated logic; repeated values are centralized (rules.md §10).
- [ ] Security implications are considered: tenant scoping, permissions, input validation, no sensitive data in logs or responses.
- [ ] Errors are handled explicitly and leave no inconsistent state.
- [ ] Relevant tests, `ruff`, `mypy` and migration checks were actually run and pass.
- [ ] Performance (query count, N+1, pagination) and maintainability were reviewed.
- [ ] No unnecessary dependencies, files, abstractions or code were introduced.
- [ ] The report accurately reflects what was implemented and verified.

## Architecture adherence

Do not casually change: module boundaries, framework or core libraries, database (PostgreSQL/PostGIS), API contracts or versioning, the auth/token pattern, the tenant isolation strategy, the shipment state machine, or the testing strategy. Any such change must explain the reason, alternatives, impact (including on admin and driver-app), and migration plan. It must also update `architecture.md` plus the related rules, record the decision in `memory.md`, and get **owner approval** if it alters an approved or PROPOSED decision.

## Dependencies

Before adding a package: check whether Django, DRF or an existing dependency already does the job, then check maintenance, Django-version compatibility, security advisories, license and transitive weight. Compare alternatives for significant dependencies. Record the choice and reason in `architecture.md` §Technology and in `memory.md` §Dependencies. Never add a package just because generated code imported it.

## Session continuity

Repository docs are the source of truth, not chat history or auto-memory. Don't assume work is done because files exist; verify by running it. Never record a test as passed unless you ran it.

## Communication & docs style

Caveman (moderate `caveman` mode) applies to chat replies only. Docs, commit messages, code comments and PR text are written in normal, complete prose. Never compress reasoning, test results or security notes to save tokens.
