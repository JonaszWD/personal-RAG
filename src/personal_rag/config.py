"""Settings, read once from environment. No secrets have defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# PoC scope: text + PDF only. Extend deliberately, one parser at a time.
ALLOWED_EXTENSIONS = frozenset({".txt", ".md", ".pdf"})


@dataclass(frozen=True)
class Settings:
    docs_dir: Path
    data_dir: Path
    api_token: str
    ollama_url: str
    embed_model: str
    chat_model: str
    max_file_bytes: int
    max_pdf_bytes: int
    max_pdf_pages: int
    pdf_timeout_s: int
    max_files: int
    chunk_chars: int
    chunk_overlap: int
    top_k: int
    max_question_chars: int
    # remote (browser) access - off unless remote_token is set
    remote_token: str | None = None
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    login_max_failures: int = 5
    login_window_s: int = 600
    session_ttl_s: int = 43_200
    query_rate_per_min: int = 20
    max_body_bytes: int = 16_384

    @property
    def size_limits(self) -> dict[str, int]:
        return {".txt": self.max_file_bytes, ".md": self.max_file_bytes, ".pdf": self.max_pdf_bytes}

    @classmethod
    def from_env(cls) -> Settings:
        token = os.environ.get("RAG_API_TOKEN", "")
        if len(token) < 32:
            raise RuntimeError("RAG_API_TOKEN must be set and at least 32 characters")
        remote = os.environ.get("RAG_REMOTE_TOKEN", "") or None
        if remote is not None and (len(remote) < 32 or remote == token):
            raise RuntimeError("RAG_REMOTE_TOKEN must be 32+ chars and differ from RAG_API_TOKEN")
        extra_hosts = tuple(
            h.strip().lower()
            for h in os.environ.get("RAG_ALLOWED_HOSTS", "").split(",")
            if h.strip()
        )
        return cls(
            remote_token=remote,
            allowed_hosts=("127.0.0.1", "localhost", *extra_hosts),
            docs_dir=Path(os.environ.get("RAG_DOCS_DIR", "/docs")),
            data_dir=Path(os.environ.get("RAG_DATA_DIR", "/data")),
            api_token=token,
            ollama_url=os.environ.get("OLLAMA_URL", "http://ollama:11434"),
            embed_model=os.environ.get("RAG_EMBED_MODEL", "nomic-embed-text"),
            chat_model=os.environ.get("RAG_CHAT_MODEL", "llama3.2:3b"),
            max_file_bytes=int(os.environ.get("RAG_MAX_FILE_BYTES", 1_000_000)),
            max_pdf_bytes=int(os.environ.get("RAG_MAX_PDF_BYTES", 10_000_000)),
            max_pdf_pages=int(os.environ.get("RAG_MAX_PDF_PAGES", 100)),
            pdf_timeout_s=int(os.environ.get("RAG_PDF_TIMEOUT_S", 30)),
            max_files=int(os.environ.get("RAG_MAX_FILES", 200)),
            chunk_chars=int(os.environ.get("RAG_CHUNK_CHARS", 1000)),
            chunk_overlap=int(os.environ.get("RAG_CHUNK_OVERLAP", 150)),
            top_k=int(os.environ.get("RAG_TOP_K", 4)),
            max_question_chars=int(os.environ.get("RAG_MAX_QUESTION_CHARS", 2000)),
        )
