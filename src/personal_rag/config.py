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

    @property
    def size_limits(self) -> dict[str, int]:
        return {".txt": self.max_file_bytes, ".md": self.max_file_bytes, ".pdf": self.max_pdf_bytes}

    @classmethod
    def from_env(cls) -> Settings:
        token = os.environ.get("RAG_API_TOKEN", "")
        if len(token) < 32:
            raise RuntimeError("RAG_API_TOKEN must be set and at least 32 characters")
        return cls(
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
