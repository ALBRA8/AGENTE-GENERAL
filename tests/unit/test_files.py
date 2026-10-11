"""Unit tests for the files subpackage."""

import asyncio
import os
import tempfile

import pytest

from albra_seed.files import (
    ContentExtractor,
    FileHandler,
    FileMetadata,
    FileProvenance,
    content_hash,
    sniff_mime,
)


# -------------------------------------------------------------------
# 1. FileHandler reads + writes with metadata + provenance
# -------------------------------------------------------------------


def test_file_handler_read_write(tmp_path):
    handler = FileHandler(base_dir=tmp_path)
    meta = asyncio.run(handler.write("hello.txt", "hello world"))
    assert isinstance(meta, FileMetadata)
    assert meta.name == "hello.txt"
    assert meta.size == len(b"hello world")
    assert meta.mime_type == "text/plain"
    assert meta.hash

    read_meta = asyncio.run(handler.read("hello.txt"))
    assert read_meta.hash == meta.hash
    assert read_meta.verify(b"hello world") is True

    # Listing returns the new file.
    entries = asyncio.run(handler.list())
    names = [e.name for e in entries if not e.is_directory]
    assert "hello.txt" in names

    # Delete removes the file.
    deleted = asyncio.run(handler.delete("hello.txt"))
    assert deleted is True
    with pytest.raises(FileNotFoundError):
        asyncio.run(handler.read("hello.txt"))


def test_file_handler_overwrite_guard(tmp_path):
    handler = FileHandler(base_dir=tmp_path)
    asyncio.run(handler.write("lock.txt", "v1"))
    with pytest.raises(FileExistsError):
        asyncio.run(handler.write("lock.txt", "v2", overwrite=False))
    # overwrite=True replaces.
    meta = asyncio.run(handler.write("lock.txt", "v2", overwrite=True))
    assert meta.verify(b"v2") is True


# -------------------------------------------------------------------
# 2. FileMetadata hash matches content
# -------------------------------------------------------------------


def test_file_metadata_hash():
    data = b"hashable content"
    meta = FileMetadata.from_bytes(name="f.txt", path="/tmp/f.txt", data=data)
    assert meta.hash == content_hash(data)
    assert meta.size == len(data)
    assert meta.verify(data) is True
    assert meta.verify(b"tampered") is False


def test_sniff_mime_known_magics():
    assert sniff_mime(b"%PDF-1.4") == "application/pdf"
    assert sniff_mime(b"PK\x03\x04abc") == "application/zip"
    assert sniff_mime(b"\x89PNG\r\n\x1a\n") == "image/png"
    assert sniff_mime(b"plain utf-8 text") == "text/plain"
    assert sniff_mime(b"") == "inode/x-empty"


# -------------------------------------------------------------------
# 3. ContentExtractor handles plain text always
# -------------------------------------------------------------------


def test_content_extractor_plain_text():
    extractor = ContentExtractor()
    result = asyncio.run(extractor.extract_bytes(b"hello plain text"))
    assert result.ok is True
    assert result.text == "hello plain text"
    assert result.extractor == "plain-text"
    assert result.mime_type == "text/plain"


def test_content_extractor_unknown_falls_back(tmp_path):
    extractor = ContentExtractor()
    # Random binary bytes -> falls back to UTF-8 with replacement chars.
    result = asyncio.run(extractor.extract_bytes(b"\xff\xfe\xfd"))
    assert result.extractor == "fallback-text"
    assert result.error is None


def test_content_extractor_missing_file():
    extractor = ContentExtractor()
    result = asyncio.run(extractor.extract_file("/definitely/does/not/exist.txt"))
    assert result.ok is False
    assert "not found" in (result.error or "")


# -------------------------------------------------------------------
# 4. FileProvenance is tamper-evident
# -------------------------------------------------------------------


def test_file_provenance_integrity():
    prov = FileProvenance("/tmp/important.txt")
    e1 = prov.record(action="create", content=b"v1", actor="alice")
    e2 = prov.record(action="modify", content=b"v2", actor="bob")
    e3 = prov.record(action="read", content=b"v2", actor="carol")

    assert len(prov) == 3
    assert prov.verify_chain() is True
    # Chain head hash equals latest entry's content hash.
    assert prov.latest_hash() == content_hash(b"v2")

    # Tamper with the middle entry.
    prov.entries()[1].actor = "mallory"
    assert prov.verify_chain() is False


def test_file_provenance_delete_uses_empty_hash():
    prov = FileProvenance("/tmp/gone.txt")
    prov.record(action="create", content=b"data", actor="alice")
    prov.record(action="delete", content=None, actor="alice")
    assert len(prov) == 2
    assert prov.verify_chain() is True
    # Delete entry's hash equals the empty-content hash.
    assert prov.entries()[1].hash == content_hash(b"")
