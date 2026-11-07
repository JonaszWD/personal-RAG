"""HTTP API. Bearer-token auth on everything except /health."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .config import Settings
from .ingest import ingest
from .ollama import Ollama
from .rag import answer
from .security import token_matches
from .store import Store


class QueryIn(BaseModel):
    question: str


class QueryOut(BaseModel):
    answer: str
    sources: list[str]


class IngestOut(BaseModel):
    indexed: int
    unchanged: int
    removed: int


def create_app(settings: Settings | None = None, llm: Ollama | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    store = Store(cfg.data_dir / "index.sqlite")
    model = llm or Ollama(cfg.ollama_url, cfg.embed_model, cfg.chat_model)
    app = FastAPI(title="Personal RAG", docs_url=None, redoc_url=None, openapi_url=None)

    def require_token(authorization: Annotated[str, Header()] = "") -> None:
        scheme, _, supplied = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token_matches(supplied, cfg.api_token):
            raise HTTPException(status_code=401, detail="unauthorized")

    auth = Depends(require_token)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/ingest", dependencies=[auth])
    def run_ingest() -> IngestOut:
        r = ingest(cfg, store, model)
        return IngestOut(indexed=r.indexed, unchanged=r.unchanged, removed=r.removed)

    @app.post("/query", dependencies=[auth])
    def query(body: QueryIn) -> QueryOut:
        q = body.question.strip()
        if not q or len(q) > cfg.max_question_chars:
            raise HTTPException(status_code=422, detail="invalid question length")
        a = answer(q, store, model, cfg.top_k)
        return QueryOut(answer=a.text, sources=a.sources)

    return app
