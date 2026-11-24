"""Upload lifecycle (architecture §19): presign, confirm (verify and clean), attach.

Clients upload straight to the bucket with a presigned URL. Nothing is trusted until
`confirm_upload` has checked the stored object: its size, its magic bytes, and for images
its metadata. Domain services then claim a confirmed upload with `attach_upload`.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.accounts.models import User
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.core import storage
from apps.core.errors import DomainError, ErrorCode
from apps.uploads import files
from apps.uploads.constants import MAGIC_PREFIX_BYTES
from apps.uploads.models import (
    EXTENSION_BY_TYPE,
    IMAGE_TYPES,
    SIZE_LIMIT_BY_TYPE,
    Upload,
    UploadContentType,
    UploadPurpose,
    UploadStatus,
)


@dataclass(frozen=True)
class UploadTicket:
    upload: Upload
    url: str
    headers: dict[str, str]
    expires_at: datetime


def _object_key(actor: User, purpose: UploadPurpose, content_type: UploadContentType) -> str:
    # Built only from server values (rules.md §3). The random name makes keys unguessable
    # even without the presigned signature.
    now = timezone.now()
    return (
        f"company/{actor.company.public_id}/{purpose.value}/{now:%Y/%m}/"
        f"{uuid.uuid4().hex}.{EXTENSION_BY_TYPE[content_type]}"
    )


def create_upload(
    *, actor: User, purpose: UploadPurpose, content_type: UploadContentType, size: int
) -> UploadTicket:
    """Register an upload and return the presigned PUT URL, valid for exactly `size` bytes
    of `content_type`."""
    if size > SIZE_LIMIT_BY_TYPE[content_type]:
        raise DomainError(ErrorCode.UPLOAD_TOO_LARGE)
    ttl = settings.UPLOAD_URL_TTL_SECONDS
    with transaction.atomic():
        upload = Upload.objects.create(
            company=actor.company,
            uploaded_by=actor,
            purpose=purpose,
            content_type=content_type,
            size=size,
            object_key=_object_key(actor, purpose, content_type),
        )
        url = storage.presign_put(
            upload.object_key, content_type=content_type.value, size=size, ttl_seconds=ttl
        )
    return UploadTicket(
        upload=upload,
        url=url,
        headers={"Content-Type": content_type.value},
        expires_at=timezone.now() + timedelta(seconds=ttl),
    )


def confirm_upload(*, actor: User, upload_id: uuid.UUID, request: HttpRequest) -> Upload:
    """Verify the uploaded object and mark the upload confirmed. Only the uploader can.

    A rejected file is deleted from the bucket together with its row, and the rejection
    is audited; that is committed before the error is raised, so it is never rolled back.
    Repeating a successful confirm is a no-op.
    """
    rejection: ErrorCode | None = None
    with transaction.atomic():
        upload = get_object_or_404(
            Upload.objects.for_company(actor.company).select_for_update().filter(uploaded_by=actor),
            public_id=upload_id,
        )
        if upload.status == UploadStatus.CONFIRMED:
            return upload
        rejection = _verify_and_clean(upload)
        if rejection is None:
            upload.status = UploadStatus.CONFIRMED
            upload.confirmed_at = timezone.now()
            upload.save(update_fields=["status", "confirmed_at", "etag", "size", "updated_at"])
        else:
            storage.delete(upload.object_key)
            # IDs and the reason only; never file names or contents (rules.md §3).
            audit.record(
                AuditAction.UPLOAD_REJECTED,
                company=actor.company,
                actor=actor,
                object_type="upload",
                object_id=str(upload.public_id),
                after={"reason": rejection.value, "content_type": upload.content_type},
                request=request,
            )
            upload.delete()
    if rejection is not None:
        raise DomainError(rejection)
    return upload


def _verify_and_clean(upload: Upload) -> ErrorCode | None:
    """Check the stored object and strip image metadata. Returns the rejection reason, or
    None when the upload is good (its `etag` and `size` are then updated, not yet saved).
    Raises when the object is missing or the store is down: nothing is changed then, so the
    client can retry."""
    content_type = UploadContentType(upload.content_type)
    info = storage.head(upload.object_key)
    if info is None:
        raise DomainError(ErrorCode.UPLOAD_NOT_FOUND)
    if info.size > SIZE_LIMIT_BY_TYPE[content_type]:
        return ErrorCode.UPLOAD_TOO_LARGE
    if info.size != upload.size:
        return ErrorCode.UPLOAD_SIZE_MISMATCH
    prefix = storage.read_prefix(upload.object_key, MAGIC_PREFIX_BYTES)
    if files.detect_content_type(prefix) != content_type:
        return ErrorCode.UPLOAD_TYPE_MISMATCH
    if content_type in IMAGE_TYPES:
        original = storage.read(upload.object_key)
        try:
            cleaned = files.strip_metadata(content_type, original)
        except files.MalformedFile:
            return ErrorCode.UPLOAD_TYPE_MISMATCH
        if cleaned != original:
            info = storage.put(upload.object_key, cleaned, content_type=content_type.value)
    upload.etag = info.etag
    upload.size = info.size
    return None


def _ensure_unchanged(upload: Upload) -> None:
    """The object must still be the one that passed verification. A presigned PUT URL stays
    valid after confirmation until it expires, so the file could have been swapped."""
    info = storage.head(upload.object_key)
    if info is None or info.etag != upload.etag:
        raise DomainError(ErrorCode.UPLOAD_NOT_CONFIRMED)


def attach_upload(*, actor: User, upload_id: uuid.UUID, purpose: UploadPurpose) -> Upload:
    """Claim a confirmed upload for a domain object. Call inside the transaction that
    creates that object, so a failure leaves the upload free to use again.

    Another company's, another user's or a differently-purposed upload is answered as a
    missing reference, so nothing about it is revealed.
    """
    with transaction.atomic():
        upload = (
            Upload.objects.for_company(actor.company)
            .select_for_update()
            .filter(public_id=upload_id, uploaded_by=actor, purpose=purpose)
            .first()
        )
        if upload is None:
            raise DomainError(ErrorCode.VALIDATION_INVALID_REFERENCE)
        if upload.status != UploadStatus.CONFIRMED:
            raise DomainError(ErrorCode.UPLOAD_NOT_CONFIRMED)
        if upload.attached_at is not None:
            raise DomainError(ErrorCode.UPLOAD_IN_USE)
        _ensure_unchanged(upload)
        upload.attached_at = timezone.now()
        upload.save(update_fields=["attached_at", "updated_at"])
    return upload


def presign_download(upload: Upload) -> tuple[str, datetime]:
    """A short-lived download URL for an attached upload. The caller must already have
    checked that the user may see the object that owns the file."""
    if upload.status != UploadStatus.CONFIRMED:
        raise DomainError(ErrorCode.UPLOAD_NOT_CONFIRMED)
    _ensure_unchanged(upload)
    ttl = settings.UPLOAD_URL_TTL_SECONDS
    content_type = UploadContentType(upload.content_type)
    url = storage.presign_get(
        upload.object_key,
        content_type=content_type.value,
        filename=f"download.{EXTENSION_BY_TYPE[content_type]}",
        ttl_seconds=ttl,
    )
    return url, timezone.now() + timedelta(seconds=ttl)
