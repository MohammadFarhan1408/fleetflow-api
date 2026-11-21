import uuid

from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PublicIdModel(models.Model):
    """Adds the non-enumerable ID exposed by the API (architecture §6).

    UUIDv7 is time-ordered, so it also serves as a stable cursor-pagination key without
    revealing the internal integer primary key.
    """

    public_id = models.UUIDField(default=uuid.uuid7, unique=True, editable=False)

    class Meta:
        abstract = True
