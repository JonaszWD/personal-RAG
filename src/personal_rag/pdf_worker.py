"""Child process: PDF bytes on stdin -> plain text on stdout. Do not import elsewhere."""

from __future__ import annotations

import contextlib
import io
import resource
import sys

from .pdf import MAX_OUTPUT_BYTES, MEMORY_LIMIT_BYTES


def _limit() -> None:
    # Best effort: RLIMIT_AS is unsupported/ignored on some platforms (macOS).
    for res, val in (
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_AS, MEMORY_LIMIT_BYTES),
        (resource.RLIMIT_FSIZE, 0),
        (resource.RLIMIT_NOFILE, 16),
    ):
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(res, (val, val))


def main() -> None:
    _limit()
    from pypdf import PdfReader  # import after limits so import cost counts

    max_pages = int(sys.argv[1])
    reader = PdfReader(io.BytesIO(sys.stdin.buffer.read()))
    if reader.is_encrypted:
        sys.exit(3)
    out: list[str] = []
    size = 0
    for page in reader.pages[:max_pages]:
        text = page.extract_text() or ""
        out.append(text)
        size += len(text)
        if size > MAX_OUTPUT_BYTES:
            break
    sys.stdout.write("\n\n".join(out))


if __name__ == "__main__":
    main()
