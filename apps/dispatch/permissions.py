"""Permission codenames owned by the dispatch module (rules.md §10).

Role assignments are seeded by `migrations/0002_seed_permissions.py` (memory D-API-29).
`dispatch.all_branches` lifts the home-branch scope for routes; `dispatch.override_capacity`
lets an assignment go over the vehicle's capacity, with a reason (migration 0004).
"""

DISPATCH_VIEW = "dispatch.view"
DISPATCH_MANAGE = "dispatch.manage"
DISPATCH_ALL_BRANCHES = "dispatch.all_branches"
DISPATCH_OVERRIDE_CAPACITY = "dispatch.override_capacity"
