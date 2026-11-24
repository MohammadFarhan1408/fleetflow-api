import pytest
from django.conf import settings
from django.test import Client
from pytest_django import Settings
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.core import storage
from apps.tenancy.factories import CompanyFactory
from apps.tenancy.models import Company
from apps.uploads.constants import MAX_IMAGE_BYTES, MAX_PDF_BYTES
from apps.uploads.models import Upload, UploadStatus
from apps.uploads.tests.conftest import (
    GPS_MARKER,
    JPEG_PICTURE,
    UPLOADS_URL,
    confirm_url,
    make_jpeg,
    make_pdf,
    make_png,
    put_to_presigned_url,
    start_upload,
    upload_file,
)

pytestmark = pytest.mark.django_db

JPEG = "image/jpeg"
PNG = "image/png"
PDF = "application/pdf"


def stored(upload: Upload) -> bytes:
    return storage.read(upload.object_key)


# --- creating an upload -------------------------------------------------------------------


def test_create_returns_a_presigned_put_for_exactly_this_file(client: APIClient) -> None:
    response = start_upload(client, JPEG, 1234)

    assert response.status_code == 201
    body = response.json()
    upload = Upload.objects.get(public_id=body["id"])
    assert upload.status == UploadStatus.PENDING
    assert (upload.content_type, upload.size) == (JPEG, 1234)
    assert body["method"] == "PUT"
    assert body["headers"] == {"Content-Type": JPEG}
    assert "X-Amz-Signature" in body["upload_url"]
    assert upload.object_key in body["upload_url"]
    assert body["expires_at"]


def test_object_key_is_server_built_and_hides_internal_ids(
    client: APIClient, company: Company
) -> None:
    body = start_upload(client, PDF, 100).json()

    key = Upload.objects.get(public_id=body["id"]).object_key
    assert key.startswith(f"company/{company.public_id}/fleet_document/")
    assert key.endswith(".pdf")
    assert f"company/{company.id}/" not in key


@pytest.mark.parametrize(
    ("content_type", "limit"),
    [(JPEG, MAX_IMAGE_BYTES), (PNG, MAX_IMAGE_BYTES), (PDF, MAX_PDF_BYTES)],
)
def test_size_limit_is_inclusive_per_type(client: APIClient, content_type: str, limit: int) -> None:
    assert start_upload(client, content_type, limit).status_code == 201

    over = start_upload(client, content_type, limit + 1)

    assert over.status_code == 400
    assert over.json()["code"] == "upload.too_large"


def test_25_mb_photo_is_rejected_before_any_upload(client: APIClient) -> None:
    response = start_upload(client, JPEG, 25 * 1024 * 1024)

    assert response.status_code == 400
    assert response.json()["code"] == "upload.too_large"
    assert Upload.objects.count() == 0


@pytest.mark.parametrize(
    "body",
    [
        {"purpose": "fleet_document", "content_type": "image/jpeg", "size": 0},
        {"purpose": "fleet_document", "content_type": "image/jpeg", "size": -5},
        {"purpose": "fleet_document", "content_type": "image/jpeg", "size": "big"},
        {"purpose": "fleet_document", "content_type": "application/x-msdownload", "size": 10},
        {"purpose": "fleet_document", "content_type": "image/gif", "size": 10},
        {"purpose": "profile_picture", "content_type": "image/jpeg", "size": 10},
        {"purpose": "fleet_document", "size": 10},
        {},
    ],
)
def test_create_validates_input(client: APIClient, body: dict[str, object]) -> None:
    response = client.post(UPLOADS_URL, body, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "validation.invalid"
    assert Upload.objects.count() == 0


def test_create_requires_authentication() -> None:
    response = APIClient().post(UPLOADS_URL, {}, format="json")

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (RoleCode.OWNER, True),
        (RoleCode.ADMIN, True),
        (RoleCode.OPS_MANAGER, True),
        (RoleCode.FLEET_MANAGER, True),
        (RoleCode.DISPATCHER, False),
        (RoleCode.WAREHOUSE_STAFF, False),
        (RoleCode.FINANCE, False),
        (RoleCode.CUSTOMER_SERVICE, False),
        (RoleCode.DRIVER, False),
        (RoleCode.VIEWER, False),
    ],
)
def test_role_matrix_for_creating_and_confirming(role: RoleCode, allowed: bool) -> None:
    user = UserFactory.create(roles=[role])
    client = client_for(user)

    created = start_upload(client, JPEG, 10)
    confirmed = client.post(confirm_url("00000000-0000-7000-8000-000000000000"))

    if allowed:
        assert created.status_code == 201
        assert confirmed.status_code == 404  # permitted, but no such upload
    else:
        assert created.status_code == 403
        assert confirmed.status_code == 403
        assert created.json()["code"] == "permission.denied"


def test_creation_is_throttled_per_user(client: APIClient) -> None:
    for _ in range(60):
        assert start_upload(client, JPEG, 10).status_code == 201

    response = start_upload(client, JPEG, 10)

    assert response.status_code == 429
    assert response.json()["code"] == "throttled"


def test_the_store_refuses_a_different_size_or_type_than_was_signed(client: APIClient) -> None:
    data = make_jpeg()
    ticket = start_upload(client, JPEG, len(data)).json()

    wrong_type = put_to_presigned_url(ticket, data, {"Content-Type": PDF})
    wrong_size = put_to_presigned_url(ticket, data + b"extra", ticket["headers"])

    assert wrong_type == 403
    assert wrong_size == 403
    key = Upload.objects.get(public_id=ticket["id"]).object_key
    assert storage.head(key) is None


# --- confirming ---------------------------------------------------------------------------


def test_good_jpeg_is_confirmed_and_exif_is_stripped(client: APIClient) -> None:
    data = make_jpeg(exif=True, extra_bytes=3 * 1024 * 1024)  # a 3 MB photo
    ticket = upload_file(client, JPEG, data)

    response = client.post(confirm_url(ticket["id"]))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["confirmed_at"]
    upload = Upload.objects.get(public_id=ticket["id"])
    cleaned = stored(upload)
    assert GPS_MARKER in data
    assert GPS_MARKER not in cleaned
    assert cleaned.endswith(b"\x00" * 100 + JPEG_PICTURE[-2:])  # picture data kept
    assert upload.size == len(cleaned) < len(data)
    assert upload.etag == storage.head(upload.object_key).etag  # type: ignore[union-attr]
    assert upload.confirmed_at is not None


def test_png_exif_is_stripped(client: APIClient) -> None:
    ticket = upload_file(client, PNG, make_png(exif=True))

    assert client.post(confirm_url(ticket["id"])).status_code == 200

    cleaned = stored(Upload.objects.get(public_id=ticket["id"]))
    assert GPS_MARKER not in cleaned
    assert b"IDAT" in cleaned


def test_pdf_is_confirmed_unchanged(client: APIClient) -> None:
    pdf = make_pdf()
    ticket = upload_file(client, PDF, pdf)

    assert client.post(confirm_url(ticket["id"])).status_code == 200

    assert stored(Upload.objects.get(public_id=ticket["id"])) == pdf


def test_confirm_twice_is_a_no_op(client: APIClient) -> None:
    ticket = upload_file(client, JPEG, make_jpeg(exif=True))
    first = client.post(confirm_url(ticket["id"]))
    etag = Upload.objects.get(public_id=ticket["id"]).etag

    second = client.post(confirm_url(ticket["id"]))

    assert second.status_code == 200
    assert second.json() == first.json()
    assert Upload.objects.get(public_id=ticket["id"]).etag == etag


def test_confirm_before_the_file_arrives_can_be_retried(client: APIClient) -> None:
    data = make_jpeg()
    ticket = start_upload(client, JPEG, len(data)).json()

    early = client.post(confirm_url(ticket["id"]))

    assert early.status_code == 400
    assert early.json()["code"] == "upload.not_found"
    assert Upload.objects.get(public_id=ticket["id"]).status == UploadStatus.PENDING
    assert put_to_presigned_url(ticket, data) == 200
    assert client.post(confirm_url(ticket["id"])).status_code == 200


def test_executable_renamed_to_jpeg_is_rejected_and_deleted(client: APIClient) -> None:
    exe = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200
    ticket = upload_file(client, JPEG, exe)
    key = Upload.objects.get(public_id=ticket["id"]).object_key

    response = client.post(confirm_url(ticket["id"]))

    assert response.status_code == 400
    assert response.json()["code"] == "upload.type_mismatch"
    assert storage.head(key) is None  # the object is gone
    assert not Upload.objects.filter(public_id=ticket["id"]).exists()
    entry = AuditLog.objects.get(action=AuditAction.UPLOAD_REJECTED)  # survives the 400
    assert entry.after == {"reason": "upload.type_mismatch", "content_type": JPEG}
    assert entry.object_id == ticket["id"]


def test_executable_declared_as_pdf_is_rejected_and_deleted(client: APIClient) -> None:
    # PDFs are not parsed, so the magic-byte check is the only thing that stops this one.
    ticket = upload_file(client, PDF, b"MZ\x90\x00" + b"\x00" * 200)
    key = Upload.objects.get(public_id=ticket["id"]).object_key

    response = client.post(confirm_url(ticket["id"]))

    assert response.json()["code"] == "upload.type_mismatch"
    assert storage.head(key) is None


def test_pdf_declared_as_png_is_rejected(client: APIClient) -> None:
    ticket = upload_file(client, PNG, make_pdf())

    response = client.post(confirm_url(ticket["id"]))

    assert response.json()["code"] == "upload.type_mismatch"


def test_malformed_image_is_rejected_and_deleted(client: APIClient) -> None:
    broken = b"\xff\xd8\xff\xe1\xff\xff" + b"x" * 50  # JPEG header, impossible segment length
    ticket = upload_file(client, JPEG, broken)
    key = Upload.objects.get(public_id=ticket["id"]).object_key

    response = client.post(confirm_url(ticket["id"]))

    assert response.status_code == 400
    assert response.json()["code"] == "upload.type_mismatch"
    assert storage.head(key) is None


def test_object_over_the_limit_is_rejected_at_confirm_even_if_presign_was_bypassed(
    client: APIClient,
) -> None:
    ticket = start_upload(client, JPEG, 1000).json()
    key = Upload.objects.get(public_id=ticket["id"]).object_key
    storage.put(key, b"\xff\xd8\xff" + b"0" * MAX_IMAGE_BYTES, content_type=JPEG)  # limit + 3

    response = client.post(confirm_url(ticket["id"]))

    assert response.status_code == 400
    assert response.json()["code"] == "upload.too_large"
    assert storage.head(key) is None


def test_object_with_another_size_than_declared_is_rejected(client: APIClient) -> None:
    ticket = start_upload(client, JPEG, 1000).json()
    key = Upload.objects.get(public_id=ticket["id"]).object_key
    storage.put(key, make_jpeg(), content_type=JPEG)

    response = client.post(confirm_url(ticket["id"]))

    assert response.json()["code"] == "upload.size_mismatch"
    assert storage.head(key) is None


def test_the_exact_size_limits_are_accepted(client: APIClient) -> None:
    jpeg = make_jpeg(extra_bytes=MAX_IMAGE_BYTES - len(make_jpeg()))
    pdf = make_pdf() + b"0" * (MAX_PDF_BYTES - len(make_pdf()))
    assert (len(jpeg), len(pdf)) == (MAX_IMAGE_BYTES, MAX_PDF_BYTES)

    for content_type, data in ((JPEG, jpeg), (PDF, pdf)):
        ticket = upload_file(client, content_type, data)
        assert client.post(confirm_url(ticket["id"])).status_code == 200, content_type


# --- who can confirm ----------------------------------------------------------------------


def test_confirming_someone_elses_upload_is_404_even_in_the_same_company(
    client: APIClient, company: Company
) -> None:
    ticket = upload_file(client, JPEG, make_jpeg())
    colleague = client_for(UserFactory.create(company=company, roles=[RoleCode.OWNER]))

    response = colleague.post(confirm_url(ticket["id"]))

    assert response.status_code == 404
    assert Upload.objects.get(public_id=ticket["id"]).status == UploadStatus.PENDING


def test_confirming_another_companys_upload_is_404(client: APIClient) -> None:
    ticket = upload_file(client, JPEG, make_jpeg())
    outsider = client_for(
        UserFactory.create(company=CompanyFactory.create(), roles=[RoleCode.OWNER])
    )

    assert outsider.post(confirm_url(ticket["id"])).status_code == 404


# --- storage outage -----------------------------------------------------------------------


def test_storage_outage_answers_503_and_keeps_the_upload(
    client: APIClient, settings: Settings
) -> None:
    ticket = upload_file(client, JPEG, make_jpeg())
    settings.S3_ENDPOINT_URL = "http://127.0.0.1:1"  # nothing listens here

    response = client.post(confirm_url(ticket["id"]))

    assert response.status_code == 503
    assert response.json()["code"] == "storage.unavailable"
    assert "127.0.0.1" not in response.content.decode()
    assert Upload.objects.get(public_id=ticket["id"]).status == UploadStatus.PENDING


def test_readiness_ignores_the_public_endpoint(client: Client) -> None:
    # The probe uses the server-side endpoint; a bad public host must not fail readiness.
    assert settings.S3_ENDPOINT_URL
    assert client.get("/health/ready").status_code == 200
