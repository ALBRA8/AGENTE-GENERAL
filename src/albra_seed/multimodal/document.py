"""Document adapter — handles PDF and DOCX content. Lazy-imports the
appropriate parser library (`pypdf` for PDF, `python-docx` for DOCX).
"""

from __future__ import annotations

import importlib
import io
from typing import Any

from albra_seed.core.types import ModalityType
from albra_seed.multimodal.base import (
    ModalityAdapter,
    ModalityNotConfiguredError,
    ModalityPayload,
    ModalityValidationError,
)

_PDF_MODULE = "pypdf"
_DOCX_MODULE = "docx"


class DocumentAdapter(ModalityAdapter):
    """Encodes/decodes document content (PDF / DOCX).

    Plain text is always available (no extra deps). Binary PDF/DOCX
    payloads require their respective libraries to be installed; absence
    is reported via `is_ready` and raises on decode.
    """

    modality = ModalityType.DOCUMENT
    name = "document"

    def __init__(self) -> None:
        super().__init__()

    # -- readiness probes (per-parser) ------------------------------
    def is_pdf_ready(self) -> bool:
        try:
            importlib.import_module(_PDF_MODULE)
            return True
        except ImportError:
            return False

    def is_docx_ready(self) -> bool:
        try:
            importlib.import_module(_DOCX_MODULE)
            return True
        except ImportError:
            return False

    def _probe_ready(self) -> bool:
        # Plain-text documents always work; binary parsers are optional.
        return True

    def _parser_ready_for(self, mime_type: str) -> bool:
        if "pdf" in mime_type:
            return self.is_pdf_ready()
        if "word" in mime_type or "docx" in mime_type:
            return self.is_docx_ready()
        return True

    # -- validation --------------------------------------------------
    def validate(self, content: Any) -> bool:
        if content is None:
            return False
        if isinstance(content, str):
            return True
        if isinstance(content, (bytes, bytearray)):
            return len(content) > 0
        return False

    # -- encode ------------------------------------------------------
    async def encode(self, content: Any) -> ModalityPayload:
        if not self.validate(content):
            raise ModalityValidationError(
                f"document adapter cannot encode {type(content).__name__}"
            )
        if isinstance(content, str):
            return ModalityPayload(
                modality=self.modality,
                mime_type="text/plain",
                encoding="utf-8",
                data=content,
                metadata={"length": len(content)},
            )
        data = bytes(content)
        mime = _sniff_mime(data)
        return ModalityPayload(
            modality=self.modality,
            mime_type=mime,
            encoding="binary",
            data=data,
            metadata={"length_bytes": len(data)},
        )

    # -- decode ------------------------------------------------------
    async def decode(self, payload: ModalityPayload) -> str:
        if payload.modality != self.modality:
            raise ModalityValidationError(
                f"payload modality mismatch: {payload.modality} != {self.modality}"
            )
        if isinstance(payload.data, str) and payload.encoding == "utf-8":
            return payload.data
        if not isinstance(payload.data, (bytes, bytearray)):
            raise ModalityValidationError(
                "document payload data must be bytes or text"
            )
        mime = payload.mime_type
        data = bytes(payload.data)
        if mime == "text/plain":
            return data.decode("utf-8", errors="replace")
        if "pdf" in mime:
            return self._decode_pdf(data)
        if "word" in mime or "docx" in mime:
            return self._decode_docx(data)
        # Fallback: best-effort text decode.
        try:
            return data.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover
            raise ModalityValidationError(f"document decode failed: {exc}") from exc

    # -- per-format decoders ----------------------------------------
    def _decode_pdf(self, data: bytes) -> str:
        if not self.is_pdf_ready():
            raise ModalityNotConfiguredError(
                "pypdf is not installed; install `pypdf` to decode PDF documents"
            )
        pypdf = importlib.import_module(_PDF_MODULE)
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            parts: list[str] = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
            return "\n".join(parts).strip()
        except Exception as exc:  # pragma: no cover - defensive
            raise ModalityValidationError(f"PDF decode failed: {exc}") from exc

    def _decode_docx(self, data: bytes) -> str:
        if not self.is_docx_ready():
            raise ModalityNotConfiguredError(
                "python-docx is not installed; install `python-docx` to decode DOCX"
            )
        docx = importlib.import_module(_DOCX_MODULE)
        try:
            doc = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as exc:  # pragma: no cover - defensive
            raise ModalityValidationError(f"DOCX decode failed: {exc}") from exc


def _sniff_mime(data: bytes) -> str:
    """Tiny magic-byte sniffer for PDF / DOCX."""
    if data.startswith(b"%PDF"):
        return "application/pdf"
    # DOCX is a ZIP; PK\x03\x04 is the ZIP local-file header.
    if data.startswith(b"PK\x03\x04"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


__all__ = ["DocumentAdapter"]
