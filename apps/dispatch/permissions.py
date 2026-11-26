"""Permission codenames owned by the dispatch module (rules.md §10).

Role assignments are seeded by `migrations/0002_seed_permissions.py` (memory D-API-29).
`dispatch.all_branches` lifts the home-branch scope for routes.
"""

DISPATCH_VIEW = "dispatch.view"
DISPATCH_MANAGE = "dispatch.manage"
DISPATCH_ALL_BRANCHES = "dispatch.all_branches"
