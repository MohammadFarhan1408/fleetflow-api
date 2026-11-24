import time
import uuid
from unittest import mock

import pytest
import urllib3
from botocore.exceptions import BotoCoreError, ClientError
from django.core.management import call_command
from pytest_django import Settings

from apps.core import storage
from apps.core.errors import DomainError, ErrorCode


def key() -> str:
    return f"test/{uuid.uuid4().hex}.bin"


def test_put_head_read_delete_round_trip() -> None:
    k = key()

    written = storage.put(k, b"hello world", content_type="application/pdf")

    info = storage.head(k)
    assert info is not None
    assert (info.size, info.etag) == (11, written.etag)
    assert storage.read(k) == b"hello world"
    assert storage.read_prefix(k, 5) == b"hello"
    storage.delete(k)
    assert storage.head(k) is None


def test_head_of_a_missing_object_is_none_and_delete_is_idempotent() -> None:
    k = key()

    assert storage.head(k) is None
    storage.delete(k)  # no error


def test_overwrite_changes_the_etag() -> None:
    k = key()
    first = storage.put(k, b"one", content_type="application/pdf")

    second = storage.put(k, b"two", content_type="application/pdf")

    assert first.etag != second.etag
    storage.delete(k)


def test_bucket_is_private(settings: Settings) -> None:
    k = key()
    storage.put(k, b"secret", content_type="application/pdf")

    anonymous = urllib3.PoolManager().request(
        "GET", f"{settings.S3_ENDPOINT_URL}/{settings.S3_BUCKET}/{k}"
    )

    assert anonymous.status == 403
    storage.delete(k)


def test_presigned_get_expires() -> None:
    k = key()
    storage.put(k, b"data", content_type="application/pdf")
    url = storage.presign_get(k, content_type="application/pdf", filename="a.pdf", ttl_seconds=1)
    assert urllib3.PoolManager().request("GET", url).status == 200

    time.sleep(2.5)

    assert urllib3.PoolManager().request("GET", url).status == 403
    storage.delete(k)


def test_ensure_bucket_is_idempotent() -> None:
    assert storage.ensure_bucket() is False  # the session fixture already created it


def test_ensure_upload_bucket_command_reports_state(capsys: pytest.CaptureFixture[str]) -> None:
    call_command("ensure_upload_bucket")

    assert "Bucket already exists." in capsys.readouterr().out


def test_ensure_bucket_creates_a_missing_bucket(settings: Settings) -> None:
    settings.S3_BUCKET = f"fleetflow-test-{uuid.uuid4().hex[:12]}"

    assert storage.bucket_ok() is False
    assert storage.ensure_bucket() is True
    assert storage.bucket_ok() is True
    storage._server().delete_bucket(Bucket=settings.S3_BUCKET)  # clean up


def test_unreachable_store_raises_the_storage_error_without_leaking_details(
    settings: Settings,
) -> None:
    settings.S3_ENDPOINT_URL = "http://127.0.0.1:1"

    for call in (lambda: storage.head("x"), lambda: storage.read("x"), lambda: storage.delete("x")):
        with pytest.raises(DomainError) as error:
            call()
        assert error.value.error_code == ErrorCode.STORAGE_UNAVAILABLE
        assert "127.0.0.1" not in str(error.value.detail)


def test_read_prefix_and_put_report_an_outage_too(settings: Settings) -> None:
    settings.S3_ENDPOINT_URL = "http://127.0.0.1:1"

    with pytest.raises(DomainError) as read_error:
        storage.read_prefix("x", 4)
    with pytest.raises(DomainError) as put_error:
        storage.put("x", b"1", content_type="application/pdf")

    assert read_error.value.error_code == ErrorCode.STORAGE_UNAVAILABLE
    assert put_error.value.error_code == ErrorCode.STORAGE_UNAVAILABLE


def test_ensure_bucket_reraises_errors_other_than_a_missing_bucket() -> None:
    denied = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadBucket")
    with mock.patch.object(storage, "_server") as server:
        server.return_value.head_bucket.side_effect = denied

        with pytest.raises(ClientError):
            storage.ensure_bucket()

        server.return_value.create_bucket.assert_not_called()


def test_head_reports_non_404_client_errors_as_unavailable() -> None:
    denied = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")
    with mock.patch.object(storage, "_server") as server:
        server.return_value.head_object.side_effect = denied

        with pytest.raises(DomainError) as error:
            storage.head("x")

    assert error.value.error_code == ErrorCode.STORAGE_UNAVAILABLE


@pytest.mark.parametrize("which", ["presign_put", "presign_get"])
def test_presigning_failure_is_reported_as_unavailable(which: str) -> None:
    with mock.patch.object(storage, "_public") as public:
        public.return_value.generate_presigned_url.side_effect = BotoCoreError()

        with pytest.raises(DomainError) as error:
            if which == "presign_put":
                storage.presign_put("x", content_type="image/jpeg", size=1, ttl_seconds=60)
            else:
                storage.presign_get("x", content_type="image/jpeg", filename="a", ttl_seconds=60)

    assert error.value.error_code == ErrorCode.STORAGE_UNAVAILABLE
