"""Scan docs dir, chunk, embed, store. Incremental via content hash.

Failures are per-file: one bad file or a transient Ollama error never aborts the batch.
Progress is published through `IngestStatus` so callers can poll instead of holding
one long HTTP request open.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings
from .ollama import Ollama
from .pdf import PdfError, extract_pdf_text
from .security import iter_safe_files
from .store import Store

log = logging.getLogger(__name__)
EMBED_BATCH = 16  # chunks per Ollama request: bounds latency per call on CPU


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    if overlap >= size:
        raise ValueError("overlap must be smaller than chunk size")
    text = text.strip()
    step = size - overlap
    return [c for i in range(0, max(len(text), 1), step) if (c := text[i : i + size].strip())]


@dataclass
class IngestStatus:
    running: bool = False
    total: int = 0
    done: int = 0
    current: str = ""
    indexed: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: list[str] = field(default_factory=list)
    error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def reset(self) -> None:
        with self._lock:
            self.running = True
            self.total = self.done = self.indexed = self.unchanged = self.removed = 0
            self.current = ""
            self.failed = []
            self.error = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "total": self.total,
                "done": self.done,
                "current": self.current,
                "indexed": self.indexed,
                "unchanged": self.unchanged,
                "removed": self.removed,
                "failed": list(self.failed),
                "error": self.error,
            }


def _embed_all(llm: Ollama, chunks: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        out.extend(llm.embed(chunks[i : i + EMBED_BATCH]))
    return out


def ingest(
    settings: Settings, store: Store, llm: Ollama, status: IngestStatus | None = None
) -> IngestStatus:
    st = status or IngestStatus()
    if not st.running:
        st.reset()
    files = list(iter_safe_files(settings.docs_dir, settings.size_limits, settings.max_files))
    st.total = len(files)
    seen: set[str] = set()
    for path in files:
        rel = str(path.relative_to(settings.docs_dir))
        seen.add(rel)
        st.current = rel
        try:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if store.file_hash(rel) == digest:
                st.unchanged += 1
            else:
                if path.suffix.lower() == ".pdf":
                    text = extract_pdf_text(raw, settings.max_pdf_pages, settings.pdf_timeout_s)
                else:
                    text = raw.decode("utf-8")
                chunks = chunk_text(text, settings.chunk_chars, settings.chunk_overlap)
                if chunks:
                    store.replace_file(rel, digest, chunks, _embed_all(llm, chunks))
                    st.indexed += 1
                    log.info("indexed %s (%d chunks)", rel, len(chunks))  # never log content
        except (UnicodeDecodeError, PdfError, httpx.HTTPError, sqlite3.Error, OSError) as e:
            log.warning("failed %s: %s", rel, type(e).__name__)
            st.failed.append(rel)
        st.done += 1
    for rel in store.known_paths() - seen:
        store.delete_file(rel)
        st.removed += 1
    st.current = ""
    return st
