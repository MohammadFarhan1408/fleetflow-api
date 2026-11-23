"""Permission codenames owned by the shipments module (rules.md §10)."""

SHIPMENTS_VIEW = "shipments.view"
# Also covers editing and deleting drafts and rotating the public tracking token.
SHIPMENTS_CREATE = "shipments.create"
# Without it, users see and create shipments only in their own branches (architecture §8).
SHIPMENTS_ALL_BRANCHES = "shipments.all_branches"
