"""The only module that talks to the S3-compatible object store (architecture §19).

Objects live in one private bucket and are reached only through short-lived presigned
URLs. Callers pass server-built keys, never client input (rules.md §3). boto3 ships no type
information, so its client is an `Any` confined to this module.
"""

import logging
from dataclasses import dataclass
from functools import cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from apps.core.errors import DomainError, ErrorCode

logger = logging.getLogger(__name__)

# Keeps requests (and the readiness probe) from hanging on an unreachable store.
_TIMEOUT_SECONDS = 5
_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    etag: str


@cache
def _client(endpoint_url: str) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        config=Config(
            signature_version="s3v4",
            # Path style works with every S3-compatible server and needs no wildcard DNS.
            s3={"addressing_style": "path"},
            connect_timeout=_TIMEOUT_SECONDS,
            read_timeout=_TIMEOUT_SECONDS,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _server() -> Any:
    return _client(settings.S3_ENDPOINT_URL)


def _public() -> Any:
    return _client(settings.S3_PUBLIC_ENDPOINT_URL)


def _is_not_found(error: ClientError) -> bool:
    code = str(error.response.get("Error", {}).get("Code", ""))
    return code in _NOT_FOUND_CODES


def _unavailable(operation: str, error: Exception) -> DomainError:
    # Class and operation only: never endpoints, keys or credentials (rules.md §3).
    logger.warning("Object storage %s failed: %s", operation, type(error).__name__, exc_info=True)
    return DomainError(ErrorCode.STORAGE_UNAVAILABLE)


def bucket_ok() -> bool:
    """Readiness probe: the bucket exists and the credentials work."""
    try:
        _server().head_bucket(Bucket=settings.S3_BUCKET)
    except BotoCoreError, ClientError:
        logger.warning("Readiness check failed: storage", exc_info=True)
        return False
    return True


def ensure_bucket() -> bool:
    """Create the bucket when missing. Returns True if it was created."""
    try:
        _server().head_bucket(Bucket=settings.S3_BUCKET)
    except ClientError as error:
        if not _is_not_found(error):
            raise
        _server().create_bucket(Bucket=settings.S3_BUCKET)
        return True
    return False


def presign_put(key: str, *, content_type: str, size: int, ttl_seconds: int) -> str:
    """URL for one PUT of exactly `size` bytes of `content_type`. Both are part of the
    signature, so the store rejects any other length or type."""
    try:
        url: str = _public().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.S3_BUCKET,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": size,
            },
            ExpiresIn=ttl_seconds,
        )
    except (BotoCoreError, ClientError) as error:
        raise _unavailable("presign_put", error) from error
    return url


def presign_get(key: str, *, content_type: str, filename: str, ttl_seconds: int) -> str:
    """URL for downloading one object as an attachment (never rendered in the browser)."""
    try:
        url: str = _public().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.S3_BUCKET,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=ttl_seconds,
        )
    except (BotoCoreError, ClientError) as error:
        raise _unavailable("presign_get", error) from error
    return url


def head(key: str) -> ObjectInfo | None:
    """Size and ETag of an object, or None when it does not exist."""
    try:
        response = _server().head_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as error:
        if _is_not_found(error):
            return None
        raise _unavailable("head", error) from error
    except BotoCoreError as error:
        raise _unavailable("head", error) from error
    return ObjectInfo(size=int(response["ContentLength"]), etag=str(response["ETag"]).strip('"'))


def read_prefix(key: str, length: int) -> bytes:
    """The first `length` bytes, enough to check a file's magic bytes."""
    try:
        response = _server().get_object(
            Bucket=settings.S3_BUCKET, Key=key, Range=f"bytes=0-{length - 1}"
        )
        data: bytes = response["Body"].read()
    except (BotoCoreError, ClientError) as error:
        raise _unavailable("read_prefix", error) from error
    return data


def read(key: str) -> bytes:
    try:
        response = _server().get_object(Bucket=settings.S3_BUCKET, Key=key)
        data: bytes = response["Body"].read()
    except (BotoCoreError, ClientError) as error:
        raise _unavailable("read", error) from error
    return data


def put(key: str, data: bytes, *, content_type: str) -> ObjectInfo:
    """Write (or overwrite) an object and return its new ETag."""
    try:
        response = _server().put_object(
            Bucket=settings.S3_BUCKET, Key=key, Body=data, ContentType=content_type
        )
    except (BotoCoreError, ClientError) as error:
        raise _unavailable("put", error) from error
    return ObjectInfo(size=len(data), etag=str(response["ETag"]).strip('"'))


def delete(key: str) -> None:
    """Delete an object. Deleting a missing key is not an error."""
    try:
        _server().delete_object(Bucket=settings.S3_BUCKET, Key=key)
    except (BotoCoreError, ClientError) as error:
        raise _unavailable("delete", error) from error
