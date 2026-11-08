import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from personal_rag.api import COOKIE, create_app
from personal_rag.auth import SessionStore, SlidingWindow
from personal_rag.config import Settings

FULL = "f" * 40
REMOTE = "r" * 40
HOST = "testserver"
ORIGIN = {"Origin": f"https://{HOST}"}


class FakeLLM:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def chat(self, system: str, user: str) -> str:
        return "answer"

    def chat_stream(self, system: str, user: str) -> Iterator[str]:
        yield from ("hel", "lo")


def make(tmp_path: Path, **over: Any) -> TestClient:
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "n.md").write_text("I like Python")
    base: dict[str, Any] = {
        "docs_dir": docs,
        "data_dir": tmp_path / "data",
        "api_token": FULL,
        "ollama_url": "",
        "embed_model": "",
        "chat_model": "",
        "max_file_bytes": 1000,
        "max_pdf_bytes": 1000,
        "max_pdf_pages": 5,
        "pdf_timeout_s": 5,
        "max_files": 10,
        "chunk_chars": 100,
        "chunk_overlap": 10,
        "top_k": 2,
        "max_question_chars": 100,
        "remote_token": REMOTE,
        "allowed_hosts": (HOST,),
    }
    base.update(over)
    return TestClient(create_app(Settings(**base), FakeLLM()), base_url=f"https://{HOST}")  # type: ignore[arg-type]


def login(c: TestClient, token: str = REMOTE) -> Any:
    return c.post("/auth/login", json={"token": token}, headers=ORIGIN)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    c = make(tmp_path)
    c.post("/ingest?wait=true", headers={"Authorization": f"Bearer {FULL}"})
    return c


def test_static_ui_and_security_headers(client: TestClient) -> None:
    for path in ("/", "/app.js", "/style.css", "/health"):
        r = client.get(path)
        assert r.status_code == 200
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["cache-control"] == "no-store"
    html = client.get("/").text
    assert "<script>" not in html and "style=" not in html  # CSP-compatible: no inline
    assert "innerHTML" not in client.get("/app.js").text


def test_host_allowlist(client: TestClient) -> None:
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400


def test_login_sets_hardened_cookie_and_grants_read_only(client: TestClient) -> None:
    r = login(client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert COOKIE.lower() in cookie
    for flag in ("httponly", "secure", "samesite=strict", "path=/"):
        assert flag in cookie
    assert "domain" not in cookie
    assert client.get("/auth/me").json() == {"scope": "read"}
    assert client.get("/documents").json()["indexed"] == {"n.md": 1}
    assert client.post("/ingest", headers=ORIGIN).status_code == 403


def test_full_token_cannot_login_to_browser(client: TestClient) -> None:
    assert login(client, FULL).status_code == 401


def test_remote_bearer_is_read_only(client: TestClient) -> None:
    h = {"Authorization": f"Bearer {REMOTE}"}
    assert client.post("/ingest", headers=h).status_code == 403
    assert client.get("/documents", headers=h).status_code == 200
    assert (
        client.post("/ingest?wait=true", headers={"Authorization": f"Bearer {FULL}"}).status_code
        == 202
    )


def test_unauthenticated_rejected(client: TestClient) -> None:
    for method, path in (("get", "/documents"), ("post", "/query"), ("post", "/query/stream")):
        r = getattr(client, method)(path)
        assert r.status_code in (401, 422)
    assert client.get("/documents").status_code == 401
    assert client.post("/query", json={"question": "q"}).status_code == 401


def test_login_lockout(tmp_path: Path) -> None:
    c = make(tmp_path)
    for _ in range(5):
        assert login(c, "x" * 40).status_code == 401
    r = login(c)  # even the right token is refused while locked
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_login_requires_matching_origin(client: TestClient) -> None:
    assert client.post("/auth/login", json={"token": REMOTE}).status_code == 403
    bad = {"Origin": "https://evil.example"}
    assert client.post("/auth/login", json={"token": REMOTE}, headers=bad).status_code == 403


def test_csrf_on_cookie_posts(client: TestClient) -> None:
    login(client)
    body = {"question": "python?"}
    assert client.post("/query", json=body).status_code == 403  # no Origin
    evil = {"Origin": "https://evil.example"}
    assert client.post("/query", json=body, headers=evil).status_code == 403
    assert client.post("/query", json=body, headers=ORIGIN).status_code == 200


def test_remote_disabled_when_no_token(tmp_path: Path) -> None:
    c = make(tmp_path, remote_token=None)
    assert login(c).status_code == 503


def test_logout_revokes_session(client: TestClient) -> None:
    login(client)
    assert client.post("/auth/logout", headers=ORIGIN).status_code == 200
    assert client.get("/documents").status_code == 401


def test_stream(client: TestClient) -> None:
    login(client)
    r = client.post("/query/stream", json={"question": "python?"}, headers=ORIGIN)
    msgs = [json.loads(line) for line in r.text.splitlines()]
    assert msgs[0] == {"sources": ["n.md"]}
    assert "".join(m.get("token", "") for m in msgs) == "hello"
    assert msgs[-1] == {"done": True}


def test_body_size_and_question_limits(client: TestClient) -> None:
    h = {"Authorization": f"Bearer {FULL}"}
    assert client.post("/query", json={"question": "x" * 20_000}, headers=h).status_code == 413
    assert client.post("/query", json={"question": "x" * 101}, headers=h).status_code == 422


def test_query_rate_limit(tmp_path: Path) -> None:
    c = make(tmp_path, query_rate_per_min=2)
    h = {"Authorization": f"Bearer {FULL}"}
    codes = [c.post("/query", json={"question": "q"}, headers=h).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_audit_log_has_no_content(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="personal_rag.audit")
    h = {"Authorization": f"Bearer {FULL}"}
    client.post("/query", json={"question": "SECRET-QUESTION-TEXT"}, headers=h)
    text = caplog.text
    assert "path=/query status=200 scope=full" in text
    assert "SECRET-QUESTION-TEXT" not in text and FULL not in text


def test_sliding_window_and_sessions_expire() -> None:
    now = [0.0]
    w = SlidingWindow(2, 10, clock=lambda: now[0])
    assert w.allow() and w.allow() and not w.allow()
    assert w.retry_after() == 10
    now[0] = 11
    assert w.allow()
    s = SessionStore(5, max_sessions=2, clock=lambda: now[0])
    a = s.create()
    assert s.valid(a)
    now[0] += 6
    assert not s.valid(a)
    ids = [s.create() for _ in range(3)]
    assert not s.valid(ids[0]) and s.valid(ids[2])  # oldest evicted at cap


def test_ingest_survives_failing_file_and_reports_it(tmp_path: Path) -> None:
    import httpx

    class Flaky(FakeLLM):
        def embed(self, texts: list[str]) -> list[list[float]]:
            if any("BOOM" in t for t in texts):
                raise httpx.ReadTimeout("slow")
            return super().embed(texts)

    docs = tmp_path / "docs"
    docs.mkdir()
    for name, body in (("a.md", "fine"), ("b.md", "BOOM"), ("c.md", "also fine")):
        (docs / name).write_text(body)
    cfg = Settings(
        docs_dir=docs, data_dir=tmp_path / "d2", api_token=FULL, ollama_url="", embed_model="",
        chat_model="", max_file_bytes=1000, max_pdf_bytes=1000, max_pdf_pages=5, pdf_timeout_s=5,
        max_files=10, chunk_chars=100, chunk_overlap=10, top_k=2, max_question_chars=100,
        allowed_hosts=(HOST,),
    )  # fmt: skip
    flaky = TestClient(create_app(cfg, Flaky()), base_url=f"https://{HOST}")  # type: ignore[arg-type]
    r = flaky.post("/ingest?wait=true", headers={"Authorization": f"Bearer {FULL}"}).json()
    assert r["running"] is False and r["total"] == 3 and r["done"] == 3
    assert r["indexed"] == 2 and r["failed"] == ["b.md"]  # b failed, c still processed
    again = flaky.get("/ingest/status", headers={"Authorization": f"Bearer {FULL}"}).json()
    assert again["failed"] == ["b.md"]
