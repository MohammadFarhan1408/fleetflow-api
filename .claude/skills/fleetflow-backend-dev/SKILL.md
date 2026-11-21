---
name: fleetflow-backend-dev
description: FleetFlow API implementation checklist. Use when writing or changing Django/DRF code in fleetflow-api (models, services, selectors, serializers, views, permissions, Celery tasks, migrations). Not for docs-only or test-only tasks.
---

# FleetFlow backend development

Use this as a checklist. The detail lives in the docs; read only the sections you need.

1. **Phase gate**: confirm the task is in the current phase ([phases.md](../../../phases.md)). If it is out of phase, stop and tell the user.
2. **Find before you write**: `rg` for existing services, selectors, permission classes, error codes and factories in the target app and in `apps/core`.
3. **Place code by layer** (architecture.md §4–5):
   - writes and rules → `services.py` (atomic, `select_for_update` on contended rows, side effects via `on_commit`/outbox)
   - reads → `selectors.py`, always starting from the tenant-scoped manager
   - views stay thin; serializers validate
4. **Tenant and permission**: company comes from `request.user`; cross-tenant access returns 404; there is a DRF permission class plus an object check in the service (architecture §8–9).
5. **State changes** only through the transition services (architecture §11, §13). Never assign `status` directly.
6. **Contract**: new or changed fields and enums → OpenAPI schema plus changelog tag; tell the sibling apps (rules.md §6).
7. **Idempotency** for driver-app and creation mutations (architecture §20).
8. **Migrations**: run `makemigrations --check`; follow expand/backfill/contract for big tables.
9. **Tests**: load `fleetflow-backend-testing`.
10. **Finish**: run ruff, mypy and pytest for the touched apps; update memory.md with the actual results.
