import uuid
from datetime import timedelta

import pytest
import urllib3
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.test import RequestFactory
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.core import storage
from apps.core.errors import DomainError, ErrorCode
from apps.tenancy.factories import CompanyFactory
from apps.uploads import services
from apps.uploads.factories import UploadFactory
from apps.uploads.models import Upload, UploadContentType, UploadPurpose
from apps.uploads.tasks import purge_unattached_uploads
from apps.uploads.tests.conftest import make_jpeg

pytestmark = pytest.mark.django_db

PURPOSE = UploadPurpose.FLEET_DOCUMENT


def confirmed_upload(actor: User, data: bytes | None = None) -> Upload:
    """A confirmed upload with its object in the bucket, made through the real services."""
    data = data or make_jpeg(exif=True)
    ticket = services.create_upload(
        actor=actor, purpose=PURPOSE, content_type=UploadContentType.JPEG, size=len(data)
    )
    storage.put(ticket.upload.object_key, data, content_type=UploadContentType.JPEG.value)
    return services.confirm_upload(
        actor=actor, upload_id=ticket.upload.public_id, request=_request()
    )


def _request() -> HttpRequest:
    return RequestFactory().post("/api/v1/uploads/x/confirm/")


@pytest.fixture
def actor() -> User:
    return UserFactory.create(roles=[RoleCode.FLEET_MANAGER])


# --- attach_upload ------------------------------------------------------------------------


def test_attach_claims_a_confirmed_upload(actor: User) -> None:
    upload = confirmed_upload(actor)

    attached = services.attach_upload(actor=actor, upload_id=upload.public_id, purpose=PURPOSE)

    assert attached.attached_at is not None


def test_attach_twice_is_in_use(actor: User) -> None:
    upload = confirmed_upload(actor)
    services.attach_upload(actor=actor, upload_id=upload.public_id, purpose=PURPOSE)

    with pytest.raises(DomainError) as error:
        services.attach_upload(actor=actor, upload_id=upload.public_id, purpose=PURPOSE)

    assert error.value.error_code == ErrorCode.UPLOAD_IN_USE


def test_attach_requires_confirmation(actor: User) -> None:
    ticket = services.create_upload(
        actor=actor, purpose=PURPOSE, content_type=UploadContentType.JPEG, size=100
    )

    with pytest.raises(DomainError) as error:
        services.attach_upload(actor=actor, upload_id=ticket.upload.public_id, purpose=PURPOSE)

    assert error.value.error_code == ErrorCode.UPLOAD_NOT_CONFIRMED


def test_attach_hides_other_users_other_companies_and_unknown_uploads(actor: User) -> None:
    upload = confirmed_upload(actor)
    colleague = UserFactory.create(company=actor.company, roles=[RoleCode.OWNER])
    outsider = UserFactory.create(roles=[RoleCode.OWNER])

    for who, upload_id in (
        (colleague, upload.public_id),
        (outsider, upload.public_id),
        (actor, uuid.uuid4()),
    ):
        with pytest.raises(DomainError) as error:
            services.attach_upload(actor=who, upload_id=upload_id, purpose=PURPOSE)
        assert error.value.error_code == ErrorCode.VALIDATION_INVALID_REFERENCE
    upload.refresh_from_db()
    assert upload.attached_at is None


def test_attach_rejects_a_different_purpose(actor: User) -> None:
    upload = confirmed_upload(actor)
    other_purpose = (
        "not_fleet_document"  # the DB allows one purpose today, so only the filter is tested
    )

    with pytest.raises(DomainError) as error:
        services.attach_upload(
            actor=actor,
            upload_id=upload.public_id,
            purpose=other_purpose,  # type: ignore[arg-type]
        )

    assert error.value.error_code == ErrorCode.VALIDATION_INVALID_REFERENCE


def test_a_file_swapped_after_confirmation_cannot_be_attached_or_downloaded(actor: User) -> None:
    # The presigned PUT URL outlives confirmation; the stored fingerprint catches a swap.
    upload = confirmed_upload(actor)
    storage.put(upload.object_key, b"MZ not a picture", content_type="image/jpeg")

    for action in (
        lambda: services.attach_upload(actor=actor, upload_id=upload.public_id, purpose=PURPOSE),
        lambda: services.presign_download(upload),
    ):
        with pytest.raises(DomainError) as error:
            action()
        assert error.value.error_code == ErrorCode.UPLOAD_NOT_CONFIRMED


# --- presign_download ---------------------------------------------------------------------


def test_download_url_serves_the_cleaned_file_as_an_attachment(actor: User) -> None:
    upload = confirmed_upload(actor)

    url, expires_at = services.presign_download(upload)

    response = urllib3.PoolManager().request("GET", url)
    assert response.status == 200
    assert response.data == storage.read(upload.object_key)
    assert response.headers["Content-Disposition"] == 'attachment; filename="download.jpg"'
    assert response.headers["Content-Type"] == "image/jpeg"
    assert expires_at > timezone.now()


def test_download_url_does_not_work_without_its_signature(actor: User) -> None:
    upload = confirmed_upload(actor)
    url, _ = services.presign_download(upload)

    response = urllib3.PoolManager().request("GET", url.split("?")[0])

    assert response.status == 403


def test_download_requires_a_confirmed_upload(actor: User) -> None:
    ticket = services.create_upload(
        actor=actor, purpose=PURPOSE, content_type=UploadContentType.JPEG, size=100
    )

    with pytest.raises(DomainError) as error:
        services.presign_download(ticket.upload)

    assert error.value.error_code == ErrorCode.UPLOAD_NOT_CONFIRMED


# --- purge --------------------------------------------------------------------------------


def age(upload: Upload, hours: int) -> None:
    Upload.objects.filter(pk=upload.pk).update(created_at=timezone.now() - timedelta(hours=hours))


def test_purge_deletes_old_unattached_uploads_and_their_objects(actor: User) -> None:
    pending = services.create_upload(
        actor=actor, purpose=PURPOSE, content_type=UploadContentType.JPEG, size=10
    ).upload
    storage.put(pending.object_key, b"x", content_type="image/jpeg")
    confirmed = confirmed_upload(actor)
    for upload in (pending, confirmed):
        age(upload, 25)

    assert purge_unattached_uploads() == 2

    assert Upload.objects.count() == 0
    assert storage.head(pending.object_key) is None
    assert storage.head(confirmed.object_key) is None


def test_purge_keeps_fresh_and_attached_uploads(actor: User) -> None:
    fresh = confirmed_upload(actor)
    attached = confirmed_upload(actor)
    services.attach_upload(actor=actor, upload_id=attached.public_id, purpose=PURPOSE)
    age(attached, 100)
    just_inside = confirmed_upload(actor)
    age(just_inside, 23)

    assert purge_unattached_uploads() == 0

    assert Upload.objects.count() == 3
    assert all(storage.head(u.object_key) for u in (fresh, attached, just_inside))


def test_purge_twice_deletes_once(actor: User) -> None:
    old = confirmed_upload(actor)
    age(old, 30)

    assert purge_unattached_uploads() == 1
    assert purge_unattached_uploads() == 0


def test_purge_keeps_the_row_when_storage_is_down(actor: User, settings: Settings) -> None:
    old = confirmed_upload(actor)
    age(old, 30)
    settings.S3_ENDPOINT_URL = "http://127.0.0.1:1"

    assert purge_unattached_uploads() == 0

    assert Upload.objects.filter(pk=old.pk).exists()  # retried by the next run


def test_purge_spans_companies(actor: User) -> None:
    other = UserFactory.create(company=CompanyFactory.create(), roles=[RoleCode.FLEET_MANAGER])
    first, second = confirmed_upload(actor), confirmed_upload(other)
    age(first, 25)
    age(second, 25)

    assert purge_unattached_uploads() == 2


# --- database constraints -----------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"size": 0},
        {"size": 10 * 1024 * 1024 + 1},  # JPEG over its limit
        {"content_type": "application/pdf", "size": 20 * 1024 * 1024 + 1},
        {"content_type": "image/gif"},
        {"purpose": "other"},
        {"status": "confirmed"},  # confirmed needs confirmed_at and an etag
        {"status": "pending", "confirmed_at": timezone.now()},
        {"attached_at": timezone.now()},  # attached while pending
    ],
)
def test_database_rejects_invalid_uploads(fields: dict[str, object]) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        UploadFactory.create(**fields)
