"""File metadata model — universal file record with provenance reference.

The metadata captures enough information to identify a file on disk AND
to verify its integrity via a content hash. Provenance is tracked
separately (see `FileProvenance`) and linked by `provenance_id`.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now


# MIME-type sniffing table (small, generic, no domain knowledge).
_SNIFF_TABLE: list[tuple[bytes, str]] = [
    (b"%PDF", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
    (b"RIFF", "audio/wav"),
    (b"OggS", "audio/ogg"),
    (b"ID3", "audio/mpeg"),
    (b"\x1f\x8b", "application/gzip"),
]


def sniff_mime(data: bytes) -> str:
    """Best-effort MIME detection from the first few bytes."""
    for magic, mime in _SNIFF_TABLE:
        if data.startswith(magic):
            return mime
    if not data:
        return "inode/x-empty"
    # Heuristic: try UTF-8 decode for plain text.
    try:
        data.decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return "application/octet-stream"


def content_hash(data: bytes | str) -> str:
    """SHA-256 hash of file content. Strings are encoded as UTF-8."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class FileMetadata(BaseModel):
    """Universal file metadata + integrity hash."""

    name: str
    path: str
    size: int = 0
    mime_type: str = "application/octet-stream"
    hash: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    modified_at: datetime = Field(default_factory=utc_now)
    provenance_id: str | None = None
    is_directory: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_bytes(
        cls,
        *,
        name: str,
        path: str,
        data: bytes,
        mime_type: str | None = None,
        provenance_id: str | None = None,
    ) -> "FileMetadata":
        return cls(
            name=name,
            path=path,
            size=len(data),
            mime_type=mime_type or sniff_mime(data),
            hash=content_hash(data),
            provenance_id=provenance_id,
        )

    def verify(self, data: bytes | str) -> bool:
        """Confirms the stored hash matches the given content."""
        if not self.hash:
            return False
        return content_hash(data) == self.hash


__all__ = ["FileMetadata", "sniff_mime", "content_hash"]
