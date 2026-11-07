"""PDF text extraction, isolated in a resource-limited child process.

PDF parsers are a classic attack surface (malformed files -> crashes, memory
bombs, parser bugs). The parent never parses: it checks magic bytes, then pipes
bytes to a child with CPU/memory rlimits and a wall-clock timeout. The child has
no filesystem path access (bytes arrive on stdin) and the container has no
internet route for it to exfiltrate over anyway. Text-only output, size-capped.
"""

from __future__ import annotations

import subprocess
import sys

MAX_OUTPUT_BYTES = 5_000_000
MEMORY_LIMIT_BYTES = 512 * 1024 * 1024


class PdfError(Exception):
    pass


def extract_pdf_text(data: bytes, max_pages: int, timeout_s: int) -> str:
    if not data.startswith(b"%PDF-"):
        raise PdfError("not a PDF")
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user-controlled args
            [sys.executable, "-I", "-m", "personal_rag.pdf_worker", str(max_pages)],
            input=data,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env={},  # empty env: nothing to leak (e.g. API token)
        )
    except subprocess.TimeoutExpired as e:
        raise PdfError("timeout") from e
    if proc.returncode != 0:
        raise PdfError(f"parser exited with {proc.returncode}")
    return proc.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
