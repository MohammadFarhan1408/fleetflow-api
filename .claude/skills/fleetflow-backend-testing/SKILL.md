---
name: fleetflow-backend-testing
description: FleetFlow API testing guide. Use when writing, reviewing or planning pytest tests in fleetflow-api, or when deciding which scenarios a backend change needs. Not needed for docs-only edits.
---

# FleetFlow backend testing

Source of truth: architecture.md §26 (stack, levels, coverage policy, scenario tables per module). Read **only the scenario table for the module you're touching** (§26.4).

Minimum per new or changed endpoint:
- 2xx happy path, with the response shape checked against the serializer
- 400 for each validation rule added (missing, bad format, boundaries)
- 401 unauthenticated; 403 wrong role (parametrize over roles)
- **404 cross-tenant**: a company B user against a company A object (and add a fixture to the tenant sweep)
- 409 for invalid state transitions and stale versions where state applies
- idempotency replay and key reuse for mutations that take `Idempotency-Key`
- `django_assert_max_num_queries` on list endpoints

Conventions:
- Real PostgreSQL + PostGIS (never SQLite); factory_boy factories with a `company` subfactory
- Mock only external HTTP and providers; Celery is eager for service tests
- Concurrency: `@pytest.mark.django_db(transaction=True)` plus threads, asserting exactly one winner
- Name tests after behavior: `test_delivered_shipment_cannot_be_dispatched_again`

Never weaken, skip or delete a failing security or permission test to get green. Report actual run results only.
