import json

import httpx
import pytest

from personal_rag import cli
from personal_rag.ollama import Ollama


def mock_ollama(handler: httpx.MockTransport) -> Ollama:
    o = Ollama("http://ollama", "emb", "chat")
    o._client = httpx.Client(base_url="http://ollama", transport=handler)  # noqa: SLF001
    return o


def test_embed_chat_and_stream() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(req.url.path)
        if req.url.path == "/api/embed":
            assert body["model"] == "emb"
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2]] * len(body["input"])})
        if body["stream"]:
            lines = [
                {"message": {"content": "he"}, "done": False},
                {"message": {"content": "llo"}, "done": False},
                {"message": {"content": ""}, "done": True},
            ]
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines) + "\n")
        return httpx.Response(200, json={"message": {"content": "hi"}})

    o = mock_ollama(httpx.MockTransport(handler))
    assert o.embed(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
    assert o.chat("sys", "user") == "hi"
    assert "".join(o.chat_stream("sys", "user")) == "hello"
    assert seen == ["/api/embed", "/api/chat", "/api/chat"]


def test_ollama_http_error_propagates() -> None:
    o = mock_ollama(httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        o.embed(["x"])


def client_for(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(base_url="http://test", transport=handler)


def test_cli_ask_prints_sanitized_answer(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answer": "ok\x1b[31m!", "sources": ["a.md\x07"]})

    cli.do_ask(client_for(httpx.MockTransport(handler)), "q?")
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "sources: a.md" in out


def test_cli_ingest_polls_until_done(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    states = iter([{"running": True, "done": 1}, {"running": False, "done": 2}])

    def snap(running: bool, failed: list[str]) -> dict[str, object]:
        return {
            "running": running, "total": 2, "done": 1, "current": "a.md", "indexed": 2,
            "unchanged": 0, "removed": 0, "failed": failed, "error": None,
        }  # fmt: skip

    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=snap(next(states)["running"], []))

    cli.do_ingest(client_for(httpx.MockTransport(handler)))
    assert "indexed 2" in capsys.readouterr().out and calls["n"] == 2


def test_cli_ingest_exits_nonzero_on_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    done = {
        "running": False, "total": 1, "done": 1, "current": "", "indexed": 0,
        "unchanged": 0, "removed": 0, "failed": ["bad.pdf"], "error": None,
    }  # fmt: skip
    h = httpx.MockTransport(lambda r: httpx.Response(200, json=done))
    with pytest.raises(SystemExit) as e:
        cli.do_ingest(client_for(h))
    assert e.value.code == 1


def test_cli_remote_shows_remote_not_full_token(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_REMOTE_TOKEN", "R" * 40)
    monkeypatch.setenv("RAG_API_TOKEN", "F" * 40)
    monkeypatch.setattr(cli, "_tailscale_dnsname", lambda: "mac.tail.ts.net")
    cli.do_remote()
    out = capsys.readouterr().out
    assert "R" * 40 in out and "F" * 40 not in out
    assert "mac.tail.ts.net" in out and "funnel" in out
