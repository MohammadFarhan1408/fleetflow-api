"""Permission codenames owned by the shipments module (rules.md §10)."""

SHIPMENTS_VIEW = "shipments.view"
# Also covers editing and deleting drafts and rotating the public tracking token.
SHIPMENTS_CREATE = "shipments.create"
# Without it, users see and create shipments only in their own branches (architecture §8).
SHIPMENTS_ALL_BRANCHES = "shipments.all_branches"
SHIPMENTS_CANCEL = "shipments.cancel"
# cancelled → created (architecture §11: recovery workflow, audited).
SHIPMENTS_RECOVER = "shipments.recover"
# Admin correction: any status, with a reason, audited (architecture §11).
SHIPMENTS_CORRECT_STATUS = "shipments.correct_status"
