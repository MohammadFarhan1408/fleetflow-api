from django.conf import settings
from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel
from apps.tenancy.models import TenantScopedModel
from apps.uploads.constants import MAX_IMAGE_BYTES, MAX_PDF_BYTES


class UploadPurpose(models.TextChoices):
    """What an upload will be attached to. Each domain adds its purpose with its feature."""

    FLEET_DOCUMENT = "fleet_document", "Fleet document"


class UploadContentType(models.TextChoices):
    JPEG = "image/jpeg", "JPEG image"
    PNG = "image/png", "PNG image"
    PDF = "application/pdf", "PDF document"


class UploadStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"


SIZE_LIMIT_BY_TYPE = {
    UploadContentType.JPEG: MAX_IMAGE_BYTES,
    UploadContentType.PNG: MAX_IMAGE_BYTES,
    UploadContentType.PDF: MAX_PDF_BYTES,
}
EXTENSION_BY_TYPE = {
    UploadContentType.JPEG: "jpg",
    UploadContentType.PNG: "png",
    UploadContentType.PDF: "pdf",
}
IMAGE_TYPES = (UploadContentType.JPEG, UploadContentType.PNG)


class Upload(TenantScopedModel, PublicIdModel, TimeStampedModel):
    """A file in the private bucket, from presigned upload to attachment (architecture §19).

    `object_key` is built by the server, never from client input. `etag` fingerprints the
    object as it was when it passed verification: a presigned PUT URL stays valid for a few
    minutes after confirmation, so attaching and downloading re-check it.
    """

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    purpose = models.CharField(max_length=32, choices=UploadPurpose.choices)
    content_type = models.CharField(max_length=32, choices=UploadContentType.choices)
    # Declared at creation; after confirmation, the size of the stored (metadata-stripped) file.
    size = models.PositiveBigIntegerField()
    object_key = models.CharField(max_length=255, unique=True)
    status = models.CharField(
        max_length=16, choices=UploadStatus.choices, default=UploadStatus.PENDING
    )
    etag = models.CharField(max_length=64, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    attached_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(purpose__in=UploadPurpose.values), name="upload_purpose_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=UploadStatus.values), name="upload_status_valid"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        content_type__in=[UploadContentType.JPEG, UploadContentType.PNG],
                        size__gt=0,
                        size__lte=MAX_IMAGE_BYTES,
                    )
                    | models.Q(
                        content_type=UploadContentType.PDF, size__gt=0, size__lte=MAX_PDF_BYTES
                    )
                ),
                name="upload_type_and_size_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=UploadStatus.PENDING, confirmed_at__isnull=True)
                    | (
                        models.Q(status=UploadStatus.CONFIRMED, confirmed_at__isnull=False)
                        & ~models.Q(etag="")
                    )
                ),
                name="upload_confirmed_state_consistent",
            ),
            models.CheckConstraint(
                condition=models.Q(attached_at__isnull=True)
                | models.Q(status=UploadStatus.CONFIRMED),
                name="upload_attached_only_when_confirmed",
            ),
        ]

    def __str__(self) -> str:
        return str(self.public_id)
