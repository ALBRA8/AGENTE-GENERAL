"""Files subpackage — universal file/document handling with metadata +
provenance + content extraction.

The SEED ships:
  * `FileHandler` — read/write/delete/list with metadata + provenance;
  * `FileMetadata` — universal metadata + content hash;
  * `ContentExtractor` — extracts plain text (always) + PDF/DOCX (lazy);
  * `FileProvenance` — tamper-evident hash-chained modification log.

NO domain knowledge — only universal file I/O.
"""

from albra_seed.files.extractor import ContentExtractor, ExtractionResult
from albra_seed.files.handler import FileHandler
from albra_seed.files.metadata import (
    FileMetadata,
    content_hash,
    sniff_mime,
)
from albra_seed.files.provenance import FileProvenance, ProvenanceEntry

__all__ = [
    # handler
    "FileHandler",
    # metadata
    "FileMetadata",
    "content_hash",
    "sniff_mime",
    # extractor
    "ContentExtractor",
    "ExtractionResult",
    # provenance
    "FileProvenance",
    "ProvenanceEntry",
]
