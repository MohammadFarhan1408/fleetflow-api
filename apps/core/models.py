import uuid

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
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


class IdempotencyRecord(models.Model):
    """A completed mutation, replayed when the same user retries with the same key.

    Records are kept 72 hours (architecture §20) and purged by a daily task.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField(null=True)
    response_body = models.JSONField(null=True, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "key"], name="idempotency_unique_user_key")
        ]

    def __str__(self) -> str:
        return self.key
