import uuid
from typing import Any, NoReturn

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


class AppendOnlyError(Exception):
    pass


class AppendOnlyQuerySet[M: models.Model](models.QuerySet[M]):
    """Blocks bulk updates and deletes on append-only tables (rules.md §2)."""

    def update(self, **kwargs: Any) -> NoReturn:
        raise AppendOnlyError(f"{self.model.__name__} rows cannot be updated.")

    def delete(self) -> NoReturn:
        raise AppendOnlyError(f"{self.model.__name__} rows cannot be deleted.")


class AppendOnlyModel(models.Model):
    """Rows can be inserted, never changed or removed. Pair with AppendOnlyQuerySet."""

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError(f"{type(self).__name__} rows cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AppendOnlyError(f"{type(self).__name__} rows cannot be deleted.")
