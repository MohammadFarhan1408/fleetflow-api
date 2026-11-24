"""Shared helpers: tiny but structurally valid files, and a real PUT to a presigned URL."""

import struct
import zlib
from typing import Any

import pytest
import urllib3
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.tenancy.factories import CompanyFactory
from apps.tenancy.models import Company

UPLOADS_URL = "/api/v1/uploads/"
GPS_MARKER = b"GPS-LATITUDE-52.5200"
JPEG_PICTURE = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x12\x34\x56" * 40 + b"\xff\xd9"


def confirm_url(public_id: Any) -> str:
    return f"{UPLOADS_URL}{public_id}/confirm/"


def _jpeg_segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def make_jpeg(*, exif: bool = False, extra_bytes: int = 0) -> bytes:
    """A JPEG skeleton: JFIF header, optional EXIF (APP1) and IPTC (APP13), an ICC profile
    (APP2), then a start-of-scan segment with picture bytes. `extra_bytes` pads the picture."""
    parts = [b"\xff\xd8", _jpeg_segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")]
    if exif:
        parts.append(_jpeg_segment(0xE1, b"Exif\x00\x00" + GPS_MARKER))
        parts.append(_jpeg_segment(0xED, b"Photoshop 3.0\x00" + GPS_MARKER))
    parts.append(_jpeg_segment(0xE2, b"ICC_PROFILE\x00\x01\x01profile"))
    parts.append(JPEG_PICTURE[:-2] + b"\x00" * extra_bytes + JPEG_PICTURE[-2:])
    return b"".join(parts)


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload)
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def make_png(*, exif: bool = False) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x10\x20\x30")
    chunks = [_png_chunk(b"IHDR", ihdr)]
    if exif:
        chunks += [
            _png_chunk(b"eXIf", b"MM\x00*" + GPS_MARKER),
            _png_chunk(b"tEXt", b"Comment\x00" + GPS_MARKER),
        ]
    chunks += [_png_chunk(b"IDAT", idat), _png_chunk(b"IEND", b"")]
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def make_pdf() -> bytes:
    return b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def put_to_presigned_url(
    ticket: dict[str, Any], data: bytes, headers: dict[str, str] | None = None
) -> int:
    """Upload like a client would: PUT to the presigned URL with the returned headers."""
    response = urllib3.PoolManager().request(
        "PUT", ticket["upload_url"], body=data, headers=headers or ticket["headers"]
    )
    return int(response.status)


@pytest.fixture
def company() -> Company:
    return CompanyFactory.create()


@pytest.fixture
def uploader(company: Company) -> User:
    return UserFactory.create(company=company, roles=[RoleCode.FLEET_MANAGER])


@pytest.fixture
def client(uploader: User) -> APIClient:
    return client_for(uploader)


def start_upload(
    client: APIClient, content_type: str, size: int, purpose: str = "fleet_document"
) -> Any:
    return client.post(
        UPLOADS_URL,
        {"purpose": purpose, "content_type": content_type, "size": size},
        format="json",
    )


def upload_file(client: APIClient, content_type: str, data: bytes) -> dict[str, Any]:
    """Create an upload and PUT `data` to it; returns the ticket."""
    response = start_upload(client, content_type, len(data))
    assert response.status_code == 201, response.content
    ticket: dict[str, Any] = response.json()
    assert put_to_presigned_url(ticket, data) == 200
    return ticket
