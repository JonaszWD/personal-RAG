"""Terminal client. Talks to the loopback-published backend.

Output is stripped of control characters so a poisoned document cannot inject
terminal escape sequences (cursor tricks, title changes, fake prompts).
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import httpx

from .project import find_project_dir, load_env

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # keeps \t and \n


def sanitize(text: str) -> str:
    return _CONTROL.sub("", text)


def backend_url() -> str:
    return (
        os.environ.get("RAG_BACKEND_URL")
        or f"http://127.0.0.1:{os.environ.get('RAG_PORT', '8000')}"
    )


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=backend_url(), headers={"Authorization": f"Bearer {token}"}, timeout=180.0
    )


def do_ingest(c: httpx.Client) -> None:
    import time

    r = c.post("/ingest")
    r.raise_for_status()
    d = r.json()
    while d["running"]:
        print(f"\rindexing {d['done']}/{d['total']}: {sanitize(d['current'])[:60]:<60}", end="")
        time.sleep(1)
        d = c.get("/ingest/status").json()
    print("\r" + " " * 80 + "\r", end="")
    print(f"indexed {d['indexed']}, unchanged {d['unchanged']}, removed {d['removed']}")
    if d["failed"]:
        print(
            f"{len(d['failed'])} failed (run again to retry): "
            + ", ".join(map(sanitize, d["failed"]))
        )
        sys.exit(1)


def do_ask(c: httpx.Client, question: str) -> None:
    r = c.post("/query", json={"question": question})
    r.raise_for_status()
    d = r.json()
    print(sanitize(d["answer"]))
    if d["sources"]:
        print("\nsources: " + ", ".join(sanitize(s) for s in d["sources"]))


def _tailscale_dnsname() -> str | None:
    import json
    import shutil
    import subprocess

    for exe in (shutil.which("tailscale"), "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if not exe or not os.path.exists(exe):
            continue
        try:
            out = subprocess.run(  # noqa: S603 - fixed argv
                [exe, "status", "--json"], capture_output=True, text=True, timeout=10, check=True
            ).stdout
            return str(json.loads(out)["Self"]["DNSName"]).rstrip(".") or None
        except (OSError, subprocess.SubprocessError, KeyError, ValueError):
            continue
    return None


def do_remote() -> None:
    """Print phone-access setup. Shows the read-only remote token (not the full token)."""
    port = os.environ.get("RAG_PORT", "8000")
    host = _tailscale_dnsname()
    name = host or "<your-mac>.<tailnet>.ts.net"
    token = os.environ.get("RAG_REMOTE_TOKEN", "")
    print("Remote (phone) access - read-only, over Tailscale only\n")
    print(f"1. Remote token (type it once on the phone):\n   {token}\n")
    print(f"2. Add to .env, then run `docker compose up -d`:\n   RAG_ALLOWED_HOSTS={name}\n")
    print("3. Publish to your tailnet only (NOT `funnel`, which is public):")
    print(f"   tailscale serve --bg {port}\n")
    print(f"4. On the phone (Tailscale on): open https://{name}/")
    if host is None:
        print("\n(Could not detect the Tailscale name - is Tailscale installed and logged in?)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="personal-rag")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("ingest", help="(re)index the docs folder")
    ask = sub.add_parser("ask", help="one-shot question")
    ask.add_argument("question")
    sub.add_parser("remote", help="show phone-access setup + remote token")
    sub.add_parser("tui", help="full-screen terminal interface (default)")
    args = parser.parse_args(argv)

    project_dir = find_project_dir()
    load_env(project_dir)
    if args.cmd in (None, "tui"):
        try:
            from .tui import run_tui
        except ImportError:
            sys.exit('TUI needs the "tui" extra: uv tool install --editable ".[tui]"')
        run_tui(project_dir)
        return
    if args.cmd == "remote":
        do_remote()
        return
    token = os.environ["RAG_API_TOKEN"]
    with _client(token) as c:
        try:
            if args.cmd == "ingest":
                do_ingest(c)
            elif args.cmd == "ask":
                do_ask(c, args.question)
        except httpx.HTTPError as e:
            sys.exit(f"error: {type(e).__name__}")


if __name__ == "__main__":
    main()
