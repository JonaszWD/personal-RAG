"""Host-side helpers: locate project, load .env, copy files into the docs folder.

The backend container only ever sees one read-only folder. "Adding a file" means
copying it (after validation) into that folder from the host, so the container
never gets broader access.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from .config import ALLOWED_EXTENSIONS
from .security import is_safe_file, iter_safe_files

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f/\\]")


def find_project_dir() -> Path:
    """Dir containing compose.yaml: env override, then source checkout, then cwd upward."""
    if override := os.environ.get("RAG_PROJECT_DIR"):
        return Path(override).expanduser().resolve()
    candidates = [Path(__file__).resolve().parents[2], *Path.cwd().resolve().parents, Path.cwd()]
    for c in candidates:
        if (c / "compose.yaml").is_file():
            return c
    raise RuntimeError("compose.yaml not found. Set RAG_PROJECT_DIR to the project folder.")


def load_env(project_dir: Path) -> None:
    """Load KEY=VALUE lines from .env without overriding real environment variables.

    Creates .env (mode 0600) with a fresh API token on first run.
    """
    env_file = project_dir / ".env"
    if not env_file.exists():
        env_file.write_text(f"RAG_API_TOKEN={secrets.token_hex(32)}\n")
        env_file.chmod(0o600)
    if "RAG_REMOTE_TOKEN=" not in env_file.read_text():
        # separate, read-only credential for the phone web UI
        with env_file.open("a") as f:
            f.write(f"RAG_REMOTE_TOKEN={secrets.token_hex(32)}\n")
        env_file.chmod(0o600)
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def host_docs_dir(project_dir: Path) -> Path:
    raw = os.environ.get("RAG_DOCS_HOST_DIR", "./documents")
    p = Path(raw).expanduser()
    return (p if p.is_absolute() else project_dir / p).resolve()


@dataclass
class ImportResult:
    added: list[Path] = field(default_factory=list)
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean(name: str) -> str:
    return _CONTROL.sub("_", name).lstrip(".") or "file"


def _place(src: Path, dest: Path, result: ImportResult) -> None:
    """Copy src to dest; skip identical content; pick a free name on conflict."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    target = dest
    while target.exists():
        if target.is_file() and not target.is_symlink() and _sha(target) == _sha(src):
            result.duplicates += 1
            return
        target = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        n += 1
    shutil.copyfile(src, target, follow_symlinks=False)
    result.added.append(target)


def _count_docs(docs_dir: Path, limits: dict[str, int], cap: int) -> int:
    if not docs_dir.is_dir():
        return 0
    return sum(1 for _ in iter_safe_files(docs_dir, limits, cap))


def import_paths(
    sources: list[Path], docs_dir: Path, limits: dict[str, int], max_files: int
) -> ImportResult:
    """Copy files and folders into docs_dir. Folders import recursively, but only
    files that pass the same safety checks as indexing (allowed type, size, no
    symlinks, no hidden files). Never raises for a bad item: errors are collected.
    """
    result = ImportResult()
    docs_dir.mkdir(parents=True, exist_ok=True)
    docs_real = docs_dir.resolve()
    room = max_files - _count_docs(docs_dir, limits, max_files)

    def check_room() -> bool:
        return len(result.added) < room

    for raw in sources:
        src = raw.expanduser()
        label = _clean(src.name)
        try:
            if src.is_symlink():
                raise ValueError("symlinks are not accepted")
            real = src.resolve(strict=True)
            if real.is_relative_to(docs_real):
                raise ValueError("already inside the docs folder")
            if src.is_dir():
                if real in (Path(real.anchor), Path.home().resolve()):
                    raise ValueError("folder too broad - pick a subfolder")
                if docs_real.is_relative_to(real):
                    raise ValueError("folder contains the docs folder")
                before = len(result.added) + result.duplicates
                root = docs_dir / label
                for f in iter_safe_files(src, limits, max_files + 1):  # +1 to detect overflow
                    if not check_room():
                        result.errors.append(f"file limit ({max_files}) reached")
                        break
                    _place(f, root / f.relative_to(src), result)
                if len(result.added) + result.duplicates == before:
                    result.errors.append(f"{label}/: no importable files")
            else:
                if src.suffix.lower() not in ALLOWED_EXTENSIONS:
                    allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
                    raise ValueError(f"unsupported type (allowed: {allowed})")
                if not is_safe_file(src, real.parent, limits):
                    raise ValueError("file too large or unreadable")
                if not check_room():
                    raise ValueError(f"file limit ({max_files}) reached")
                _place(src, docs_dir / label, result)
        except (ValueError, OSError) as e:
            result.errors.append(f"{label}: {e}")
    return result


def parse_dropped_paths(text: str) -> list[Path] | None:
    """Interpret pasted text as drag-and-drop file paths, or return None.

    Terminals deliver a drop as pasted text: shell-escaped or quoted absolute
    paths (or file:// URIs), whitespace/newline separated. Returns paths only if
    EVERY token is an absolute (or ~) path that exists, so ordinary pasted text
    (questions, snippets) is never mistaken for a drop.
    """
    text = text.strip()
    if not text or len(text) > 20_000:
        return None
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    paths: list[Path] = []
    for tok in tokens:
        if tok.startswith("file://"):
            tok = unquote(urlparse(tok).path)
        if not tok.startswith(("/", "~")):
            return None
        p = Path(tok).expanduser()
        if not p.exists():
            return None
        paths.append(p)
    return paths or None
