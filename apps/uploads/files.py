"""File-type detection and lossless metadata stripping (architecture §19).

Pure functions on bytes, so the security-relevant parsing is unit-tested without storage.
Stripping copies the picture data byte for byte and drops only the metadata segments that
can carry location or personal data: the pixels are never decoded or re-encoded.
"""

from apps.uploads.models import UploadContentType

_JPEG_SOI = b"\xff\xd8\xff"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PDF_SIGNATURE = b"%PDF-"

# JPEG APP1 holds EXIF and XMP, APP13 holds IPTC. APP2 (ICC colour profile) and APP14
# (Adobe colour transform) stay: dropping them would change how the picture looks.
_JPEG_DROP_MARKERS = frozenset({0xE1, 0xED})
_JPEG_EOI = 0xD9
_JPEG_SOS = 0xDA
# Standalone markers carry no length: TEM, RST0-RST7 and SOI.
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD9)})

# eXIf holds EXIF; tEXt, iTXt and zTXt hold free-text metadata (including XMP).
_PNG_DROP_CHUNKS = frozenset({b"eXIf", b"tEXt", b"iTXt", b"zTXt"})
_PNG_CHUNK_OVERHEAD = 12  # length, type and CRC
_PNG_MAX_CHUNK_LENGTH = 0x7FFFFFFF


class MalformedFile(ValueError):
    """The bytes do not form a valid file of the declared type."""


def detect_content_type(prefix: bytes) -> UploadContentType | None:
    """The type a file's first bytes claim to be, or None if unsupported."""
    if prefix.startswith(_JPEG_SOI):
        return UploadContentType.JPEG
    if prefix.startswith(_PNG_SIGNATURE):
        return UploadContentType.PNG
    if prefix.startswith(_PDF_SIGNATURE):
        return UploadContentType.PDF
    return None


def strip_metadata(content_type: UploadContentType, data: bytes) -> bytes:
    """`data` without EXIF, XMP and other embedded metadata. PDFs are returned unchanged:
    cleaning them needs a PDF rewriter, which is out of scope (memory D-API-25)."""
    if content_type == UploadContentType.JPEG:
        return _strip_jpeg(data)
    if content_type == UploadContentType.PNG:
        return _strip_png(data)
    return data


def _strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(_JPEG_SOI):
        raise MalformedFile("not a JPEG")
    out = bytearray(data[:2])
    pos, end = 2, len(data)
    while True:
        if pos + 2 > end or data[pos] != 0xFF:
            raise MalformedFile("expected a JPEG marker")
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte before a marker
            pos += 1
            continue
        if marker == _JPEG_EOI:
            out += data[pos:]
            return bytes(out)
        if marker in _JPEG_STANDALONE:
            out += data[pos : pos + 2]
            pos += 2
            continue
        if marker == 0x00 or pos + 4 > end:
            raise MalformedFile("invalid JPEG marker")
        length = int.from_bytes(data[pos + 2 : pos + 4], "big")
        segment_end = pos + 2 + length
        if length < 2 or segment_end > end:
            raise MalformedFile("JPEG segment exceeds the file")
        if marker not in _JPEG_DROP_MARKERS:
            out += data[pos:segment_end]
        pos = segment_end
        if marker == _JPEG_SOS:
            # Entropy-coded picture data up to the end of the file: copied verbatim.
            out += data[pos:]
            return bytes(out)


def _strip_png(data: bytes) -> bytes:
    if not data.startswith(_PNG_SIGNATURE):
        raise MalformedFile("not a PNG")
    out = bytearray(_PNG_SIGNATURE)
    pos, end = len(_PNG_SIGNATURE), len(data)
    first = True
    while pos < end:
        if pos + 8 > end:
            raise MalformedFile("truncated PNG chunk header")
        length = int.from_bytes(data[pos : pos + 4], "big")
        chunk_type = data[pos + 4 : pos + 8]
        chunk_end = pos + _PNG_CHUNK_OVERHEAD + length
        if length > _PNG_MAX_CHUNK_LENGTH or chunk_end > end:
            raise MalformedFile("PNG chunk exceeds the file")
        if first and chunk_type != b"IHDR":
            raise MalformedFile("PNG must start with IHDR")
        first = False
        if chunk_type not in _PNG_DROP_CHUNKS:
            out += data[pos:chunk_end]
        pos = chunk_end
        if chunk_type == b"IEND":
            if pos != end:
                # Bytes after IEND are how a PNG hides a second file.
                raise MalformedFile("data after IEND")
            return bytes(out)
    raise MalformedFile("PNG has no IEND chunk")
