from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from personal_rag.api import create_app
from personal_rag.config import Settings

TOKEN = "t" * 40


class FakeLLM:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "python" in t.lower() else [0.0, 1.0] for t in texts]

    def chat(self, system: str, user: str) -> str:
        return "answer from fake"


def make_client(tmp_path: Path) -> TestClient:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "n.md").write_text("I like Python")
    cfg = Settings(
        docs_dir=docs,
        data_dir=tmp_path / "data",
        api_token=TOKEN,
        ollama_url="",
        embed_model="",
        chat_model="",
        max_file_bytes=1000,
        max_pdf_bytes=100000,
        max_pdf_pages=5,
        pdf_timeout_s=10,
        max_files=10,
        chunk_chars=100,
        chunk_overlap=10,
        top_k=2,
        max_question_chars=50,
        allowed_hosts=("testserver",),
    )
    llm: Any = FakeLLM()
    return TestClient(create_app(cfg, llm))


def test_auth_required(tmp_path: Path) -> None:
    c = make_client(tmp_path)
    assert c.get("/health").status_code == 200
    assert c.post("/query", json={"question": "x"}).status_code == 401
    assert c.post("/ingest", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_ingest_and_query(tmp_path: Path) -> None:
    c = make_client(tmp_path)
    h = {"Authorization": f"Bearer {TOKEN}"}
    assert c.post("/ingest?wait=true", headers=h).json()["indexed"] == 1
    assert c.post("/ingest?wait=true", headers=h).json()["unchanged"] == 1
    r = c.post("/query", headers=h, json={"question": "python?"})
    assert r.json() == {"answer": "answer from fake", "sources": ["n.md"]}


def test_question_length_limit(tmp_path: Path) -> None:
    c = make_client(tmp_path)
    h = {"Authorization": f"Bearer {TOKEN}"}
    assert c.post("/query", headers=h, json={"question": "x" * 51}).status_code == 422
