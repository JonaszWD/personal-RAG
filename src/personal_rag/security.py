"""Path and auth guards."""

from __future__ import annotations

import hmac
from collections.abc import Iterator, Mapping
from pathlib import Path

from .config import ALLOWED_EXTENSIONS


def is_safe_file(path: Path, root: Path, size_limits: Mapping[str, int]) -> bool:
    """True only for regular, in-root, allow-listed, size-limited files.

    Symlinks are rejected outright: they can point outside the docs root.
    `size_limits` maps lowercase suffix -> max bytes.
    """
    try:
        if path.is_symlink() or not path.is_file():
            return False
        suffix = path.suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS or suffix not in size_limits:
            return False
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve(strict=True)):
            return False
        return path.stat().st_size <= size_limits[suffix]
    except OSError:
        return False


def iter_safe_files(root: Path, size_limits: Mapping[str, int], max_files: int) -> Iterator[Path]:
    count = 0
    for path in sorted(root.rglob("*")):
        # skip hidden files/dirs (.git, .ssh, .env, ...)
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if is_safe_file(path, root, size_limits):
            yield path
            count += 1
            if count >= max_files:
                return


def token_matches(supplied: str, expected: str) -> bool:
    return hmac.compare_digest(supplied.encode(), expected.encode())
