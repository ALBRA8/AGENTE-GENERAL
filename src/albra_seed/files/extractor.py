"""Content extractor — extracts plain text / structured data from files.

Always supports plain text. PDF and DOCX are extracted lazily by
delegating to `DocumentAdapter` (which lazy-imports `pypdf` /
`python-docx` as required). Unknown mime types fall back to a
best-effort UTF-8 decode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from albra_seed.files.metadata import FileMetadata, sniff_mime
from albra_seed.multimodal.document import DocumentAdapter


@dataclass
class ExtractionResult:
    """Outcome of a content extraction."""

    text: str
    mime_type: str
    structured: dict[str, Any] | None = None
    error: str | None = None
    extractor: str = "plain-text"

    @property
    def ok(self) -> bool:
        return self.error is None


class ContentExtractor:
    """Universal content extractor.

    Strategy:
      1. plain text — always works (UTF-8 decode).
      2. PDF — delegate to DocumentAdapter (lazy `pypdf` import).
      3. DOCX — delegate to DocumentAdapter (lazy `python-docx` import).
      4. anything else — best-effort UTF-8 decode with replacement.

    NEVER makes domain-specific decisions about the content's meaning.
    """

    def __init__(self, document_adapter: DocumentAdapter | None = None) -> None:
        self._doc = document_adapter or DocumentAdapter()

    async def extract_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
    ) -> ExtractionResult:
        mime = mime_type or sniff_mime(data)
        if mime == "text/plain":
            return ExtractionResult(
                text=data.decode("utf-8", errors="replace"),
                mime_type=mime,
                extractor="plain-text",
            )
        if "pdf" in mime:
            return await self._extract_via_document(data, mime)
        if "word" in mime or "docx" in mime:
            return await self._extract_via_document(data, mime)
        # Fallback: best-effort text.
        try:
            text = data.decode("utf-8", errors="replace")
            return ExtractionResult(
                text=text,
                mime_type=mime,
                extractor="fallback-text",
            )
        except Exception as exc:  # pragma: no cover - defensive
            return ExtractionResult(
                text="",
                mime_type=mime,
                error=f"extraction failed: {exc}",
                extractor="fallback-text",
            )

    async def extract_file(self, path: str) -> ExtractionResult:
        """Reads `path` from disk and extracts its content."""
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except FileNotFoundError as exc:
            return ExtractionResult(
                text="",
                mime_type="application/octet-stream",
                error=f"file not found: {path}",
                extractor="plain-text",
            )
        except OSError as exc:
            return ExtractionResult(
                text="",
                mime_type="application/octet-stream",
                error=f"read error: {exc}",
                extractor="plain-text",
            )
        return await self.extract_bytes(data)

    async def extract_metadata(self, meta: FileMetadata) -> ExtractionResult:
        if not meta.hash:
            return ExtractionResult(
                text="",
                mime_type=meta.mime_type,
                error="metadata has no hash",
                extractor="plain-text",
            )
        return await self.extract_file(meta.path)

    # -- helpers ----------------------------------------------------
    async def _extract_via_document(
        self, data: bytes, mime: str
    ) -> ExtractionResult:
        try:
            # Build a minimal payload and reuse DocumentAdapter.decode.
            from albra_seed.multimodal.base import ModalityPayload

            payload = ModalityPayload(
                modality=self._doc.modality,
                mime_type=mime,
                encoding="binary",
                data=data,
            )
            text = await self._doc.decode(payload)
            return ExtractionResult(
                text=text,
                mime_type=mime,
                extractor="document-adapter",
            )
        except Exception as exc:
            return ExtractionResult(
                text="",
                mime_type=mime,
                error=str(exc),
                extractor="document-adapter",
            )


__all__ = ["ContentExtractor", "ExtractionResult"]
