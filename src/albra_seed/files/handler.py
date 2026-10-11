"""File handler — reads/writes files with metadata + provenance tracking.

The handler is transport-agnostic: it operates on the local filesystem
via `pathlib.Path` but tracks every read/write through a `FileProvenance`
chain and returns `FileMetadata` records on every operation. NO domain
knowledge — only universal file I/O.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from albra_seed.core.types import utc_now
from albra_seed.files.metadata import FileMetadata, content_hash, sniff_mime
from albra_seed.files.provenance import FileProvenance


class FileHandler:
    """Reads/writes files with metadata + provenance."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir) if base_dir else Path.cwd()
        self._base.mkdir(parents=True, exist_ok=True)
        self._provenance: dict[str, FileProvenance] = {}

    # -- path helpers ------------------------------------------------
    def _resolve(self, path: str | Path) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self._base / p
        return p

    def _provenance_for(self, path: str) -> FileProvenance:
        if path not in self._provenance:
            self._provenance[path] = FileProvenance(path)
        return self._provenance[path]

    # -- read / write / delete --------------------------------------
    async def read(self, path: str | Path) -> FileMetadata:
        p = self._resolve(path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"file not found: {p}")
        data = p.read_bytes()
        meta = FileMetadata.from_bytes(
            name=p.name,
            path=str(p),
            data=data,
        )
        meta.created_at = _datetime_from_stat(p.stat().st_ctime)
        meta.modified_at = _datetime_from_stat(p.stat().st_mtime)
        prov = self._provenance_for(str(p))
        prov.record(action="read", content=data, actor="file-handler")
        return meta

    async def write(
        self,
        path: str | Path,
        content: bytes | str,
        *,
        overwrite: bool = True,
        actor: str = "file-handler",
    ) -> FileMetadata:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and not overwrite:
            raise FileExistsError(f"file already exists: {p}")
        if isinstance(content, str):
            content = content.encode("utf-8")
        p.write_bytes(content)
        meta = FileMetadata.from_bytes(name=p.name, path=str(p), data=content)
        action = "modify" if p.exists() else "create"
        prov = self._provenance_for(str(p))
        prov.record(action=action, content=content, actor=actor)
        return meta

    async def delete(self, path: str | Path) -> bool:
        p = self._resolve(path)
        if not p.exists():
            return False
        if p.is_file():
            p.unlink()
            prov = self._provenance_for(str(p))
            prov.record(action="delete", content=None, actor="file-handler")
            return True
        # Directory: remove recursively (no domain decision — caller's choice).
        shutil.rmtree(p)
        return True

    async def list(self, directory: str | Path | None = None) -> list[FileMetadata]:
        base = self._resolve(directory) if directory else self._base
        if not base.exists() or not base.is_dir():
            return []
        out: list[FileMetadata] = []
        for entry in sorted(base.iterdir()):
            if entry.is_file():
                stat = entry.stat()
                meta = FileMetadata(
                    name=entry.name,
                    path=str(entry),
                    size=stat.st_size,
                    mime_type=sniff_mime(entry.read_bytes()[:512]),
                    hash=content_hash(entry.read_bytes()),
                    created_at=_datetime_from_stat(stat.st_ctime),
                    modified_at=_datetime_from_stat(stat.st_mtime),
                )
                out.append(meta)
            elif entry.is_dir():
                out.append(
                    FileMetadata(
                        name=entry.name,
                        path=str(entry),
                        is_directory=True,
                    )
                )
        return out

    # -- introspection ----------------------------------------------
    def provenance(self, path: str | Path) -> FileProvenance | None:
        return self._provenance.get(str(self._resolve(path)))

    def base_dir(self) -> Path:
        return self._base


def _datetime_from_stat(ts: float):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc)


__all__ = ["FileHandler"]
