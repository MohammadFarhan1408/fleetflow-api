"""Permission codenames owned by the fleet module (rules.md §10).

Role assignments are seeded by `migrations/0002_seed_permissions.py` (memory D-API-25).
`fleet.all_branches` lifts the home-branch scope for drivers and vehicles.
"""

FLEET_VIEW = "fleet.view"
FLEET_MANAGE = "fleet.manage"
FLEET_ALL_BRANCHES = "fleet.all_branches"
