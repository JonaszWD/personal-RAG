"""HTTP API.

Two credentials:
- full token (Authorization: Bearer): everything. Stays on the Mac (CLI/TUI).
- remote token: read-only (/query, /documents). Exchanged for an HttpOnly session
  cookie by the phone web UI. Can never call /ingest.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from importlib.resources import files
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import SessionStore, SlidingWindow
from .config import Settings
from .ingest import IngestStatus, ingest
from .ollama import Ollama
from .rag import SYSTEM_PROMPT, answer, build_prompt, retrieve
from .security import token_matches
from .store import Store

audit = logging.getLogger("personal_rag.audit")
COOKIE = "__Host-rag"  # __Host- prefix: browser enforces Secure, Path=/, no Domain

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
}

PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class QueryIn(BaseModel):
    question: str


class QueryOut(BaseModel):
    answer: str
    sources: list[str]


class DocumentsOut(BaseModel):
    indexed: dict[str, int]  # relative path -> chunk count


class IngestOut(BaseModel):
    running: bool
    total: int
    done: int
    current: str
    indexed: int
    unchanged: int
    removed: int
    failed: list[str]
    error: str | None


class LoginIn(BaseModel):
    token: str = Field(max_length=256)


@dataclass(frozen=True)
class Principal:
    scope: Literal["full", "read"]
    via_cookie: bool


def create_app(settings: Settings | None = None, llm: Ollama | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    store = Store(cfg.data_dir / "index.sqlite")
    model = llm or Ollama(cfg.ollama_url, cfg.embed_model, cfg.chat_model)
    sessions = SessionStore(cfg.session_ttl_s)
    login_failures = SlidingWindow(cfg.login_max_failures, cfg.login_window_s)
    query_limiter = SlidingWindow(cfg.query_rate_per_min, 60)

    app = FastAPI(title="Personal RAG", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(cfg.allowed_hosts))

    @app.middleware("http")
    async def harden_and_audit(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.monotonic()
        request.state.scope = "anon"
        status = 500
        try:
            cl = request.headers.get("content-length")
            if cl is not None and (not cl.isdigit() or int(cl) > cfg.max_body_bytes):
                status = 413
                response: Response = JSONResponse({"detail": "body too large"}, status_code=413)
            else:
                response = await call_next(request)
                status = response.status_code
            for k, v in SECURITY_HEADERS.items():
                response.headers[k] = v
            return response
        finally:
            # metadata only - never bodies, questions or answers
            audit.info(
                "method=%s path=%s status=%s scope=%s ms=%d",
                request.method,
                request.url.path,
                status,
                request.state.scope,
                (time.monotonic() - start) * 1000,
            )

    # ---------- auth ----------
    def check_origin(request: Request) -> None:
        """CSRF defence for cookie-authenticated or login requests: Origin must match Host."""
        origin = request.headers.get("origin")
        if origin is None or urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(status_code=403, detail="bad origin")

    def current(request: Request) -> Principal:
        header = request.headers.get("authorization", "")
        if header:
            scheme, _, supplied = header.partition(" ")
            if scheme.lower() == "bearer":
                if token_matches(supplied, cfg.api_token):
                    request.state.scope = "full"
                    return Principal("full", False)
                if cfg.remote_token and token_matches(supplied, cfg.remote_token):
                    request.state.scope = "read"
                    return Principal("read", False)
            raise HTTPException(status_code=401, detail="unauthorized")
        if sessions.valid(request.cookies.get(COOKIE)):
            if request.method not in ("GET", "HEAD"):
                check_origin(request)
            request.state.scope = "read"
            return Principal("read", True)
        raise HTTPException(status_code=401, detail="unauthorized")

    def need_full(p: Principal = Depends(current)) -> Principal:  # noqa: B008
        if p.scope != "full":
            raise HTTPException(status_code=403, detail="forbidden")
        return p

    def rate_limit() -> None:
        if not query_limiter.allow():
            raise HTTPException(status_code=429, detail="slow down", headers={"Retry-After": "10"})

    def valid_question(body: QueryIn) -> str:
        q = body.question.strip()
        if not q or len(q) > cfg.max_question_chars:
            raise HTTPException(status_code=422, detail="invalid question length")
        return q

    # ---------- web UI (static, no secrets) ----------
    web = files("personal_rag") / "web"

    def make_page(filename: str, media: str) -> Callable[[], Response]:
        content = (web / filename).read_bytes()

        def page() -> Response:
            return Response(content, media_type=media)

        return page

    for route, (filename, media) in PAGES.items():
        app.add_api_route(
            route, make_page(filename, media), methods=["GET"], include_in_schema=False
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/login")
    def login(body: LoginIn, request: Request, response: Response) -> dict[str, bool]:
        if not cfg.remote_token:
            raise HTTPException(status_code=503, detail="remote access disabled")
        check_origin(request)
        wait = login_failures.retry_after()
        if wait > 0:
            raise HTTPException(
                status_code=429,
                detail="too many attempts",
                headers={"Retry-After": str(math.ceil(wait))},
            )
        # Only the remote token may create a browser session; the full token never should.
        if not token_matches(body.token, cfg.remote_token):
            login_failures.hit()
            raise HTTPException(status_code=401, detail="unauthorized")
        login_failures.reset()
        request.state.scope = "read"
        response.set_cookie(
            COOKIE,
            sessions.create(),
            max_age=cfg.session_ttl_s,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return {"ok": True}

    @app.post("/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        check_origin(request)
        sessions.revoke(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return {"ok": True}

    @app.get("/auth/me")
    def me(p: Principal = Depends(current)) -> dict[str, str]:  # noqa: B008
        return {"scope": p.scope}

    # ---------- data ----------
    @app.get("/documents")
    def documents(_: Principal = Depends(current)) -> DocumentsOut:  # noqa: B008
        return DocumentsOut(indexed=store.chunk_counts())

    ingest_status = IngestStatus()
    ingest_start = threading.Lock()
    ingest_thread: list[threading.Thread] = []

    def ingest_job() -> None:
        try:
            ingest(cfg, store, model, ingest_status)
        except Exception:  # noqa: BLE001 - last resort; never leave status stuck on "running"
            logging.getLogger(__name__).exception("ingest crashed")
            ingest_status.error = "indexing crashed - see backend logs"
        finally:
            ingest_status.running = False

    @app.post("/ingest", status_code=202, dependencies=[Depends(need_full)])
    def run_ingest(wait: bool = False) -> IngestOut:
        """Start indexing in the background (one job at a time). `wait=true` blocks until done."""
        with ingest_start:
            if not ingest_status.running:
                ingest_status.reset()
                t = threading.Thread(target=ingest_job, daemon=True)
                ingest_thread[:] = [t]
                t.start()
        if wait and ingest_thread:
            ingest_thread[0].join()
        return IngestOut(**ingest_status.snapshot())

    @app.get("/ingest/status")
    def ingest_progress(_: Principal = Depends(current)) -> IngestOut:  # noqa: B008
        return IngestOut(**ingest_status.snapshot())

    @app.post("/query", dependencies=[Depends(current), Depends(rate_limit)])
    def query(body: QueryIn) -> QueryOut:
        q = valid_question(body)
        try:
            a = answer(q, store, model, cfg.top_k)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail="model backend error") from e
        return QueryOut(answer=a.text, sources=a.sources)

    @app.post("/query/stream", dependencies=[Depends(current), Depends(rate_limit)])
    def query_stream(body: QueryIn) -> StreamingResponse:
        q = valid_question(body)
        try:
            hits = retrieve(q, store, model, cfg.top_k)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail="model backend error") from e

        def events() -> Iterator[bytes]:
            def line(obj: dict[str, object]) -> bytes:
                return (json.dumps(obj) + "\n").encode()

            yield line({"sources": sorted({h.path for h in hits})})
            if not hits:
                yield line({"token": "Index is empty. Add files first."})
                yield line({"done": True})
                return
            try:
                for piece in model.chat_stream(SYSTEM_PROMPT, build_prompt(q, hits)):
                    yield line({"token": piece})
                yield line({"done": True})
            except (httpx.HTTPError, ValueError):
                yield line({"error": "model backend error"})

        return StreamingResponse(events(), media_type="application/x-ndjson")

    return app
