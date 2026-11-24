"""Permission codenames owned by the uploads module (rules.md §10).

Role assignments are seeded by `migrations/0002_seed_permissions.py` (memory D-API-25).
What a file may be attached to is decided by the domain service that attaches it.
"""

UPLOADS_CREATE = "uploads.create"
