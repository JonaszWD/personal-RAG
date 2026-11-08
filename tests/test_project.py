from pathlib import Path

import pytest

from personal_rag.project import import_paths, load_env

LIM = {".txt": 1000, ".md": 1000, ".pdf": 1000}


def test_import_single_files_dedupe(tmp_path: Path) -> None:
    a = tmp_path / "n.md"
    a.write_text("hi")
    docs = tmp_path / "docs"
    r1 = import_paths([a], docs, LIM, 10)
    r2 = import_paths([a], docs, LIM, 10)  # identical -> skipped
    a.write_text("changed")
    r3 = import_paths([a], docs, LIM, 10)  # different content -> renamed
    assert [p.name for p in r1.added] == ["n.md"]
    assert r2.duplicates == 1 and not r2.added
    assert [p.name for p in r3.added] == ["n (1).md"]


def test_import_multiple_and_folder_filters(tmp_path: Path) -> None:
    src = tmp_path / "notes"
    (src / "sub").mkdir(parents=True)
    (src / "a.md").write_text("a")
    (src / "sub" / "b.txt").write_text("b")
    (src / "sub" / "evil.exe").write_text("x")
    (src / "big.txt").write_text("x" * 5000)
    (src / ".secret.txt").write_text("s")
    (src / ".git").mkdir()
    (src / ".git" / "c.md").write_text("c")
    (src / "link.md").symlink_to(src / "a.md")
    loose = tmp_path / "loose.txt"
    loose.write_text("l")
    docs = tmp_path / "docs"
    r = import_paths([src, loose], docs, LIM, 50)
    got = sorted(str(p.relative_to(docs)) for p in r.added)
    assert got == ["loose.txt", "notes/a.md", "notes/sub/b.txt"]
    assert r.errors == []


def test_import_rejects(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    (tmp_path / "x.exe").write_text("x")
    (tmp_path / "big.txt").write_text("x" * 2000)
    real = tmp_path / "r.txt"
    real.write_text("x")
    (tmp_path / "link.txt").symlink_to(real)
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "z.exe").write_text("z")
    names = ["x.exe", "big.txt", "link.txt", "missing.txt", "empty", ""]
    for name in names:
        res = import_paths([tmp_path / name] if name else [Path.home()], docs, LIM, 10)
        assert not res.added and len(res.errors) == 1, name


def test_import_respects_file_cap_and_docs_recursion(tmp_path: Path) -> None:
    src = tmp_path / "many"
    src.mkdir()
    for i in range(6):
        (src / f"{i}.txt").write_text(str(i))
    docs = tmp_path / "many" / "docs"  # docs inside the source folder
    assert import_paths([src], docs, LIM, 3).errors == ["many: folder contains the docs folder"]
    docs2 = tmp_path / "docs2"
    r = import_paths([src], docs2, LIM, 3)
    assert len(r.added) == 3 and "file limit (3) reached" in r.errors[0]


def test_load_env_creates_private_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_API_TOKEN", raising=False)
    load_env(tmp_path)
    env = tmp_path / ".env"
    assert env.stat().st_mode & 0o777 == 0o600
    import os

    assert len(os.environ["RAG_API_TOKEN"]) == 64
    monkeypatch.delenv("RAG_API_TOKEN")


def test_parse_dropped_paths(tmp_path: Path) -> None:
    from personal_rag.project import parse_dropped_paths

    a = tmp_path / "my file.md"
    a.write_text("x")
    b = tmp_path / "b.txt"
    b.write_text("x")
    esc = str(a).replace(" ", "\\ ")
    assert parse_dropped_paths(f"{esc} '{b}'\n") == [a, b]
    assert parse_dropped_paths(f"file://{tmp_path}/my%20file.md") == [a]
    assert parse_dropped_paths("what is my favourite language?") is None
    assert parse_dropped_paths(f"{esc} nonexistent") is None
    assert parse_dropped_paths(f"{tmp_path}/missing.md") is None
    assert parse_dropped_paths("") is None
