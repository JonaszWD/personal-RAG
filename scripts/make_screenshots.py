"""Render docs/images/tui.svg by driving the real TUI headlessly against a stub backend.

Uses only the synthetic demo corpus in sample_docs/ - never your own documents.
Run: make screenshots
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from textual.widgets import Input

ROOT = Path(__file__).resolve().parents[1]
os.environ.update(
    RAG_API_TOKEN="x" * 40,
    RAG_DOCS_HOST_DIR=str(ROOT / "sample_docs"),
    RAG_BACKEND_URL="http://stub",
)

from personal_rag import tui  # noqa: E402

DOCS = sorted(p.name for p in (ROOT / "sample_docs").glob("*.md"))
QUESTION = "At what temperature and for how long do I bake Nonna Rosa's lasagna?"
ANSWER = (
    "Bake it at 190 degrees Celsius for 45 minutes, until the top is golden and the edges "
    "bubble. Let it rest for 15 minutes before cutting.\n"
)


def stub(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    if path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if path == "/documents":
        return httpx.Response(200, json={"indexed": {d: 2 for d in DOCS}})
    if path == "/query":
        return httpx.Response(
            200, json={"answer": ANSWER, "sources": ["lasagna-recipe.md", "garden-log.md"]}
        )
    return httpx.Response(404, content=json.dumps({"detail": "n/a"}))


async def main() -> None:
    app = tui.RagApp(ROOT)
    app.client = httpx.Client(base_url="http://stub", transport=httpx.MockTransport(stub))
    async with app.run_test(size=(118, 30)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        app.query_one("#q", Input).value = QUESTION
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        out = ROOT / "docs" / "images" / "tui.svg"
        out.write_text(app.export_screenshot(title="personal-rag"))
        print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
