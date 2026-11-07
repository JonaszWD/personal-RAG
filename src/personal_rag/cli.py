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

BACKEND_URL = os.environ.get("RAG_BACKEND_URL", "http://127.0.0.1:8000")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # keeps \t and \n


def sanitize(text: str) -> str:
    return _CONTROL.sub("", text)


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=BACKEND_URL, headers={"Authorization": f"Bearer {token}"}, timeout=180.0
    )


def do_ingest(c: httpx.Client) -> None:
    r = c.post("/ingest")
    r.raise_for_status()
    d = r.json()
    print(f"indexed {d['indexed']}, unchanged {d['unchanged']}, removed {d['removed']}")


def do_ask(c: httpx.Client, question: str) -> None:
    r = c.post("/query", json={"question": question})
    r.raise_for_status()
    d = r.json()
    print(sanitize(d["answer"]))
    if d["sources"]:
        print("\nsources: " + ", ".join(sanitize(s) for s in d["sources"]))


def chat(c: httpx.Client) -> None:
    print("Ask about your files. Commands: /ingest, /quit")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return
        try:
            do_ingest(c) if line == "/ingest" else do_ask(c, line)
        except httpx.HTTPError as e:
            print(f"error: {type(e).__name__}", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="personal-rag")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("ingest", help="(re)index the docs folder")
    ask = sub.add_parser("ask", help="one-shot question")
    ask.add_argument("question")
    sub.add_parser("chat", help="interactive (default)")
    args = parser.parse_args(argv)

    token = os.environ.get("RAG_API_TOKEN", "")
    if not token:
        sys.exit("Set RAG_API_TOKEN (same value as in .env)")
    with _client(token) as c:
        try:
            if args.cmd == "ingest":
                do_ingest(c)
            elif args.cmd == "ask":
                do_ask(c, args.question)
            else:
                chat(c)
        except httpx.HTTPError as e:
            sys.exit(f"error: {type(e).__name__}")


if __name__ == "__main__":
    main()
