"""Scan docs dir, chunk, embed, store. Incremental via content hash."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from .config import Settings
from .ollama import Ollama
from .pdf import PdfError, extract_pdf_text
from .security import iter_safe_files
from .store import Store

log = logging.getLogger(__name__)


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    if overlap >= size:
        raise ValueError("overlap must be smaller than chunk size")
    text = text.strip()
    step = size - overlap
    return [c for i in range(0, max(len(text), 1), step) if (c := text[i : i + size].strip())]


@dataclass(frozen=True)
class IngestResult:
    indexed: int
    unchanged: int
    removed: int


def ingest(settings: Settings, store: Store, llm: Ollama) -> IngestResult:
    indexed = unchanged = 0
    seen: set[str] = set()
    for path in iter_safe_files(settings.docs_dir, settings.size_limits, settings.max_files):
        rel = str(path.relative_to(settings.docs_dir))
        seen.add(rel)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if store.file_hash(rel) == digest:
            unchanged += 1
            continue
        try:
            if path.suffix.lower() == ".pdf":
                text = extract_pdf_text(raw, settings.max_pdf_pages, settings.pdf_timeout_s)
            else:
                text = raw.decode("utf-8")
        except (UnicodeDecodeError, PdfError) as e:
            log.warning("skipping %s: %s", rel, type(e).__name__)
            continue
        chunks = chunk_text(text, settings.chunk_chars, settings.chunk_overlap)
        if not chunks:
            continue
        store.replace_file(rel, digest, chunks, llm.embed(chunks))
        indexed += 1
        log.info("indexed %s (%d chunks)", rel, len(chunks))  # never log content
    stale = store.known_paths() - seen
    for rel in stale:
        store.delete_file(rel)
    return IngestResult(indexed, unchanged, len(stale))
