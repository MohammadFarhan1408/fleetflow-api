"""Upload limits (architecture §19). Lives apart from the models so the DB constraints and
the services read the same numbers."""

from datetime import timedelta

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
# Rows and objects never attached to anything are deleted after this long (architecture §19).
UNATTACHED_RETENTION = timedelta(hours=24)
# Enough to recognise every supported format by its magic bytes.
MAGIC_PREFIX_BYTES = 16
