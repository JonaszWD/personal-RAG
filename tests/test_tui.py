import asyncio
from pathlib import Path

import pytest

pytest.importorskip("textual")


def test_tui_mounts_and_escapes_markup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_API_TOKEN", "t" * 40)
    monkeypatch.setenv("RAG_DOCS_HOST_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_BACKEND_URL", "http://127.0.0.1:1")  # unreachable
    from textual.widgets import RichLog

    from personal_rag import tui

    monkeypatch.setattr(tui.shutil, "which", lambda _: None)  # never start docker in tests

    async def go() -> None:
        app = tui.RagApp(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.say("[bold red]x[/] \x1b[2J")
            await pilot.pause()
            log = app.query_one("#log", RichLog)
            rendered = "\n".join(line.text for line in log.lines)
            assert "[bold red]x[/]" in rendered and "\x1b" not in rendered

    asyncio.run(go())


def test_picker_multi_mark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_API_TOKEN", "t" * 40)
    monkeypatch.setenv("RAG_BACKEND_URL", "http://127.0.0.1:1")
    from textual.app import App

    from personal_rag import tui

    root = tmp_path / "root"
    (root / "dir").mkdir(parents=True)
    (root / "a.md").write_text("a")
    (root / "b.txt").write_text("b")
    (root / "c.exe").write_text("c")  # must not be listed
    results: list[list[Path]] = []

    class Host(App[None]):
        def on_mount(self) -> None:
            self.push_screen(tui.AddFileScreen(root), results.append)

    async def go() -> None:
        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            tree = app.screen.query_one(tui.FileTree)
            names = [n.data.path.name for n in tree.root.children if n.data]
            assert "c.exe" not in names and set(names) == {"dir", "a.md", "b.txt"}
            tree.focus()
            await pilot.press("down", "space", "space")  # skip root, mark two entries
            await pilot.press("ctrl+s")
            await pilot.pause()

    asyncio.run(go())
    assert len(results) == 1 and len(results[0]) == 2
    assert all(p.parent == root for p in results[0])


def test_drop_imports_files_and_plain_paste_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    monkeypatch.setenv("RAG_API_TOKEN", "t" * 40)
    monkeypatch.setenv("RAG_DOCS_HOST_DIR", str(docs))
    monkeypatch.setenv("RAG_BACKEND_URL", "http://127.0.0.1:1")
    from textual import events
    from textual.widgets import Input

    from personal_rag import tui

    monkeypatch.setattr(tui.shutil, "which", lambda _: None)
    src = tmp_path / "dropped file.md"
    src.write_text("hello")

    async def go() -> None:
        app = tui.RagApp(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            box = app.query_one("#q", Input)
            box.post_message(events.Paste(str(src).replace(" ", "\\ ")))
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert box.value == ""  # path text was not typed into the box
            box.post_message(events.Paste("just a question"))
            await pilot.pause()
            assert box.value == "just a question"

    asyncio.run(go())
    assert (docs / "dropped file.md").read_text() == "hello"
