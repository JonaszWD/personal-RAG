"""Full-screen terminal interface (Textual). One session: start stack, add files, chat.

Security notes:
- Backend text is rendered as rich `Text` objects with markup disabled, so a
  poisoned document cannot inject styling/markup, and control chars are stripped.
- Files are only ever *copied* into the single docs folder the container mounts.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Footer, Header, Input, Label, RichLog, Static

from .cli import backend_url, sanitize
from .config import ALLOWED_EXTENSIONS, Settings
from .project import host_docs_dir, import_paths, parse_dropped_paths


class FileTree(DirectoryTree):
    """Shows folders and supported files only; hides dotfiles and symlinks.

    Space marks/unmarks the highlighted file or folder (multi-select).
    """

    BINDINGS = [Binding("space", "mark", "Mark/unmark", show=True)]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.marked: set[Path] = set()

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [
            p
            for p in paths
            if not p.name.startswith(".")
            and not p.is_symlink()
            and (p.is_dir() or p.suffix.lower() in ALLOWED_EXTENSIONS)
        ]

    def render_label(self, node: Any, base_style: Any, style: Any) -> Text:
        label = super().render_label(node, base_style, style)
        if node.data is not None and node.data.path in self.marked:
            return Text.assemble(("[x] ", "bold green"), label)
        return label

    def action_mark(self) -> None:
        node = self.cursor_node
        if node is None or node.data is None or node is self.root:
            return  # never mark the tree root (would import a whole tree)
        path = node.data.path
        self.marked.symmetric_difference_update({path})
        self._invalidate()
        self.post_message(self.MarkChanged(len(self.marked)))
        self.action_cursor_down()

    class MarkChanged(Message):
        def __init__(self, count: int) -> None:
            super().__init__()
            self.count = count


class AddFileScreen(ModalScreen[list[Path]]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "confirm", "Add marked"),
    ]
    DEFAULT_CSS = """
    AddFileScreen { align: center middle; }
    #dialog { width: 85%; height: 85%; border: thick $accent; background: $surface; padding: 1; }
    #tree { height: 1fr; }
    #count { height: 1; color: $success; }
    """

    def __init__(self, root: Path | None = None) -> None:
        super().__init__()
        self._root = root or Path.home()

    def compose(self) -> ComposeResult:
        exts = ", ".join(sorted(ALLOWED_EXTENSIONS))
        with Vertical(id="dialog"):
            yield Label(
                f"Space marks files/folders (folders import only {exts}). "
                "Ctrl+S adds all marked, Enter on a file adds it plus marked. Esc cancels."
            )
            yield Input(placeholder="...or paste/drag in one or more paths, Enter", id="path")
            yield Static("Nothing marked", id="count")
            yield FileTree(str(self._root), id="tree")
            yield Footer()

    @on(FileTree.MarkChanged)
    def _count(self, event: FileTree.MarkChanged) -> None:
        self.query_one("#count", Static).update(f"{event.count} marked")

    @on(DirectoryTree.FileSelected)
    def _picked(self, event: DirectoryTree.FileSelected) -> None:
        tree = self.query_one(FileTree)
        self.dismiss(sorted(tree.marked | {event.path}))

    @on(Input.Submitted, "#path")
    def _typed(self, event: Input.Submitted) -> None:
        try:
            parts = shlex.split(event.value)
        except ValueError:
            self.query_one("#count", Static).update("Could not parse that path")
            return
        if parts:
            self.dismiss([Path(p).expanduser() for p in parts])

    def action_confirm(self) -> None:
        marked = sorted(self.query_one(FileTree).marked)
        if marked:
            self.dismiss(marked)

    def action_cancel(self) -> None:
        self.dismiss([])


class ChatInput(Input):
    """Question box. Pasted text that is really dropped file paths is diverted."""

    class Dropped(Message):
        def __init__(self, paths: list[Path]) -> None:
            super().__init__()
            self.paths = paths

    def on_paste(self, event: events.Paste) -> None:
        paths = parse_dropped_paths(event.text)
        if paths is not None:
            event.prevent_default()  # don't insert the paths into the text box
            event.stop()
            self.post_message(self.Dropped(paths))


class RagApp(App[None]):
    TITLE = "Personal RAG"
    CSS = """
    #side { width: 36; border-right: solid $primary; padding: 0 1; }
    #docs { height: 1fr; }
    #hint { color: $text-muted; }
    #main { padding: 0 1; }
    #log { height: 1fr; }
    #status { height: 1; color: $text-muted; }
    """
    BINDINGS = [
        Binding("ctrl+a", "add_file", "Add file", priority=True),  # beat Input's ctrl+a=Home
        Binding("ctrl+r", "reindex", "Re-index"),
        Binding("ctrl+l", "clear", "Clear chat"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, project_dir: Path) -> None:
        super().__init__()
        self.project_dir = project_dir
        self.docs_dir = host_docs_dir(project_dir)
        self.limits = Settings.from_env().size_limits
        self.client = httpx.Client(
            base_url=backend_url(),
            headers={"Authorization": f"Bearer {os.environ['RAG_API_TOKEN']}"},
            timeout=300.0,
        )
        self.busy = False
        self._ingest_watching = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="side"):
                yield Label("Documents  (✓ indexed, ○ pending)")
                yield Label("Drag files here to add", id="hint")
                yield Static("", id="docs")
            with Vertical(id="main"):
                yield RichLog(id="log", markup=False, highlight=False, wrap=True)
                yield Static("", id="status")
                yield ChatInput(placeholder="Ask about your files... (Enter to send)", id="q")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#q", Input).focus()
        self.say(f"Docs folder: {_tilde(self.docs_dir)}")
        self.startup()

    # ---- UI helpers (safe to call from worker threads) ----
    def say(self, msg: str, style: str = "") -> None:
        self._ui(self.query_one("#log", RichLog).write, Text(sanitize(msg), style=style))

    def status(self, msg: str) -> None:
        self._ui(self.query_one("#status", Static).update, Text(sanitize(msg)))

    def _ui(self, fn: Any, *args: Any) -> None:
        if threading.get_ident() == self._thread_id:
            fn(*args)
        else:
            self.call_from_thread(fn, *args)

    def _set_docs(self, text: Text) -> None:
        self.query_one("#docs", Static).update(text)

    # ---- backend ----
    def _healthy(self) -> bool:
        try:
            return self.client.get("/health", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    @work(thread=True, exclusive=True, group="startup")
    def startup(self) -> None:
        if not self._healthy():
            if shutil.which("docker") is None:
                self.status("Backend down and docker not found.")
                return
            self.status("Starting backend (docker compose up -d)...")
            subprocess.run(  # noqa: S603 - fixed argv
                ["docker", "compose", "up", "-d", "--build"],  # noqa: S607
                cwd=self.project_dir,
                capture_output=True,
                timeout=900,
                check=False,
            )
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and not self._healthy():
                time.sleep(2)
        if not self._healthy():
            self.status("Backend not reachable. Check: docker compose logs backend")
            return
        self.status("Backend ready.")
        self.say("Backend ready. Ctrl+A add a file, Ctrl+R re-index, then ask questions.")
        self.refresh_docs()

    def _local_files(self) -> list[str]:
        from .security import iter_safe_files

        if not self.docs_dir.is_dir():
            return []
        return [
            str(p.relative_to(self.docs_dir))
            for p in iter_safe_files(self.docs_dir, self.limits, 200)
        ]

    @work(thread=True, group="docs")
    def refresh_docs(self) -> None:
        indexed: dict[str, Any] = {}
        try:
            r = self.client.get("/documents")
            r.raise_for_status()
            indexed = r.json()["indexed"]
        except httpx.HTTPError:
            pass
        files = self._local_files()
        out = Text()
        for f in files:
            mark = "✓" if f in indexed else "○"
            out.append(f"{mark} {sanitize(f)}\n", style="green" if f in indexed else "yellow")
        if not files:
            out.append("(none - press Ctrl+A)")
        self.call_from_thread(self._set_docs, out)

    @work(thread=True, group="ingest")
    def run_ingest(self) -> None:
        if self._ingest_watching:  # a job is already being followed; it will pick up new files
            return
        self._ingest_watching = True
        try:
            r = self.client.post("/ingest")
            r.raise_for_status()
            d = r.json()
            ticks = 0
            while d["running"]:
                self.status(f"Indexing {d['done']}/{d['total']}: {d['current']}")
                ticks += 1
                if ticks % 5 == 0:
                    self.refresh_docs()  # show files turning ✓ as they finish
                time.sleep(1)
                d = self.client.get("/ingest/status").json()
            msg = f"Indexed {d['indexed']}, unchanged {d['unchanged']}, removed {d['removed']}."
            if d["failed"]:
                msg += f" {len(d['failed'])} failed: " + ", ".join(d["failed"][:5])
                msg += " (press Ctrl+R to retry)"
            self.say(msg, "red" if d["failed"] or d["error"] else "cyan")
            if d["error"]:
                self.say(d["error"], "red")
        except httpx.HTTPError as e:
            self.say(f"Indexing failed: {_err(e)} (press Ctrl+R to retry)", "red")
        finally:
            self._ingest_watching = False
        self.status("")
        self.refresh_docs()

    @work(thread=True, group="job")
    def ask(self, question: str) -> None:
        self.status("Thinking... (first answer is slow while the model loads)")
        try:
            r = self.client.post("/query", json={"question": question})
            r.raise_for_status()
            d = r.json()
            self.say(d["answer"] + "\n")
            if d["sources"]:
                self.say("sources: " + ", ".join(d["sources"]) + "\n", "dim")
        except httpx.HTTPError as e:
            self.say(f"Query failed: {_err(e)}", "red")
        self.status("")

    # ---- actions ----
    @on(Input.Submitted, "#q")
    def _submit(self, event: Input.Submitted) -> None:
        q = event.value.strip()
        event.input.value = ""
        if q:
            self.say(f"> {q}", "bold")
            self.ask(q)

    @on(ChatInput.Dropped)
    def _dropped_on_input(self, event: ChatInput.Dropped) -> None:
        self._handle_drop(event.paths)

    def on_paste(self, event: events.Paste) -> None:
        # Drop while something other than the question box has focus.
        paths = parse_dropped_paths(event.text)
        if paths is not None:
            self._handle_drop(paths)

    def _handle_drop(self, paths: list[Path]) -> None:
        self.say(f"Dropped {len(paths)} item(s).", "dim")
        self.import_selected(paths)

    def action_add_file(self) -> None:
        self.push_screen(AddFileScreen(), self._file_chosen)

    def _file_chosen(self, paths: list[Path] | None) -> None:
        if paths:
            self.import_selected(paths)

    @work(thread=True, group="job")
    def import_selected(self, paths: list[Path]) -> None:
        self.status(f"Importing {len(paths)} item(s)...")
        cfg = Settings.from_env()
        res = import_paths(paths, self.docs_dir, self.limits, cfg.max_files)
        if res.added or res.duplicates:
            self.say(
                f"Added {len(res.added)} file(s)"
                + (f", {res.duplicates} already present" if res.duplicates else "")
                + ".",
                "cyan",
            )
        for err in res.errors:
            self.say(f"Not added: {err}", "red")
        self.status("")
        self.refresh_docs()
        if res.added:
            self.run_ingest()

    def action_reindex(self) -> None:
        self.run_ingest()

    def action_clear(self) -> None:
        self.query_one("#log", RichLog).clear()


def _tilde(p: Path) -> str:
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


def _err(e: httpx.HTTPError) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return type(e).__name__


def run_tui(project_dir: Path) -> None:
    RagApp(project_dir).run()
