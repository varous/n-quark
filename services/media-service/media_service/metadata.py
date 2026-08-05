"""Deterministic image metadata from file headers (Phase 4B).

Dependency-free: sniffs the format from magic bytes and reads dimensions from the header of PNG / JPEG
/ GIF / WEBP. No decoding, no OCR, no recognition, no colour analysis, no embeddings, no scoring.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageMeta:
    mime_type: str
    image_format: str
    width: int | None
    height: int | None

    @property
    def aspect_ratio(self) -> float | None:
        if self.width and self.height:
            return round(self.width / self.height, 4)
        return None


def sniff_format(data: bytes) -> tuple[str, str] | None:
    """Return (mime_type, format) from magic bytes, or None if not a recognized image."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "PNG"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "JPEG"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "GIF"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "WEBP"
    return None


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def _gif_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    w, h = struct.unpack("<HH", data[6:10])
    return w, h


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # Start-Of-Frame markers carry the dimensions
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seg_len
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    try:
        if chunk == b"VP8 ":
            w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
            h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
            return w, h
        if chunk == b"VP8L":
            b = data[21:25]
            bits = int.from_bytes(b, "little")
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return w, h
        if chunk == b"VP8X":
            w = int.from_bytes(data[24:27], "little") + 1
            h = int.from_bytes(data[27:30], "little") + 1
            return w, h
    except (struct.error, IndexError):
        return None
    return None


_SIZERS = {"PNG": _png_size, "GIF": _gif_size, "JPEG": _jpeg_size, "WEBP": _webp_size}


def extract(data: bytes) -> ImageMeta | None:
    """Sniff format + read dimensions from the header. Returns None for non-images."""
    sniffed = sniff_format(data)
    if sniffed is None:
        return None
    mime, fmt = sniffed
    size = _SIZERS[fmt](data)
    w, h = (size if size else (None, None))
    return ImageMeta(mime_type=mime, image_format=fmt, width=w, height=h)
