import struct
from collections.abc import Callable

import pytest

from apps.uploads.files import MalformedFile, detect_content_type, strip_metadata
from apps.uploads.models import UploadContentType
from apps.uploads.tests.conftest import GPS_MARKER, JPEG_PICTURE, make_jpeg, make_pdf, make_png

JPEG = UploadContentType.JPEG
PNG = UploadContentType.PNG
PDF = UploadContentType.PDF


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        (make_jpeg()[:16], JPEG),
        (make_png()[:16], PNG),
        (make_pdf()[:16], PDF),
        (b"MZ\x90\x00\x03\x00\x00\x00", None),  # a Windows executable
        (b"GIF89a", None),
        (b"", None),
    ],
)
def test_detect_content_type(prefix: bytes, expected: UploadContentType | None) -> None:
    assert detect_content_type(prefix) == expected


def test_jpeg_metadata_is_removed_and_the_picture_is_untouched() -> None:
    original = make_jpeg(exif=True)

    cleaned = strip_metadata(JPEG, original)

    assert GPS_MARKER in original
    assert GPS_MARKER not in cleaned
    assert b"JFIF" in cleaned  # header kept
    assert b"ICC_PROFILE" in cleaned  # colour profile kept: dropping it changes the picture
    assert cleaned.endswith(JPEG_PICTURE)  # byte-for-byte identical picture data
    assert cleaned.startswith(b"\xff\xd8")


def test_jpeg_without_metadata_is_unchanged() -> None:
    original = make_jpeg()

    assert strip_metadata(JPEG, original) == original


def test_jpeg_keeps_bytes_after_the_scan_even_when_they_look_like_markers() -> None:
    # 0xFFE1 inside entropy-coded data must not be read as an APP1 segment.
    tricky = make_jpeg(exif=True)[:-2] + b"\xff\xe1\x00\x04ab" + b"\xff\xd9"

    assert strip_metadata(JPEG, tricky).endswith(b"\xff\xe1\x00\x04ab\xff\xd9")


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xd8\xff",  # truncated right after SOI
        b"\xff\xd8\x00\x00\x00\x00",  # not a marker
        b"\xff\xd8\xff\xe1\x00\x01",  # segment length below the minimum of 2
        b"\xff\xd8\xff\xe1\xff\xff" + b"x" * 10,  # segment longer than the file
        b"\xff\xd8\xff\x00\x00\x04ab",  # marker 0x00 is invalid
        b"not a jpeg at all",
    ],
)
def test_malformed_jpeg_is_rejected(data: bytes) -> None:
    with pytest.raises(MalformedFile):
        strip_metadata(JPEG, data)


def test_png_metadata_chunks_are_removed() -> None:
    original = make_png(exif=True)

    cleaned = strip_metadata(PNG, original)

    assert GPS_MARKER in original
    assert GPS_MARKER not in cleaned
    assert b"eXIf" not in cleaned
    assert b"tEXt" not in cleaned
    assert b"IDAT" in cleaned
    assert cleaned.endswith(b"IEND\xaeB`\x82")


def test_png_without_metadata_is_unchanged() -> None:
    original = make_png()

    assert strip_metadata(PNG, original) == original


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d[:20],  # truncated mid-chunk
        lambda d: d[:8] + struct.pack(">I", 2**31) + d[12:],  # absurd chunk length
        lambda d: d[:-12],  # IEND missing
        lambda d: d + b"hidden second file",  # data after IEND
        lambda d: d[:12] + b"IDAT" + d[16:],  # first chunk is not IHDR
        lambda d: b"\x89PNG" + d[4:8] + b"junk",  # broken signature
    ],
    ids=["truncated", "huge-length", "no-iend", "trailing-data", "no-ihdr", "bad-signature"],
)
def test_malformed_png_is_rejected(mutate: Callable[[bytes], bytes]) -> None:
    with pytest.raises(MalformedFile):
        strip_metadata(PNG, mutate(make_png()))


def test_pdf_is_returned_unchanged() -> None:
    pdf = make_pdf()

    assert strip_metadata(PDF, pdf) == pdf


def test_jpeg_with_fill_bytes_standalone_markers_and_early_eoi_is_handled() -> None:
    # Fill bytes before a marker are legal and dropped; RST markers carry no length; a file
    # may end with EOI before any scan.
    data = b"\xff\xd8" + b"\xff\xff\xff\xd0" + b"\xff\xe1\x00\x06Exif" + b"\xff\xd9"

    cleaned = strip_metadata(JPEG, data)

    assert cleaned == b"\xff\xd8\xff\xd0\xff\xd9"


def test_not_a_png_signature_is_rejected_by_the_stripper() -> None:
    with pytest.raises(MalformedFile):
        strip_metadata(PNG, b"definitely not a png")


def test_not_a_jpeg_signature_is_rejected_by_the_stripper() -> None:
    with pytest.raises(MalformedFile):
        strip_metadata(JPEG, b"\x89PNG\r\n\x1a\n")
