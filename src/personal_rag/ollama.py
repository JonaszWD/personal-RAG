"""Minimal Ollama client. Talks only to the configured (internal) URL."""

from __future__ import annotations

import httpx


class Ollama:
    def __init__(self, base_url: str, embed_model: str, chat_model: str) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=120.0)
        self._embed_model = embed_model
        self._chat_model = chat_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = self._client.post("/api/embed", json={"model": self._embed_model, "input": texts})
        r.raise_for_status()
        return list(r.json()["embeddings"])

    def chat(self, system: str, user: str) -> str:
        r = self._client.post(
            "/api/chat",
            json={
                "model": self._chat_model,
                "stream": False,
                "options": {"temperature": 0.1},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        r.raise_for_status()
        return str(r.json()["message"]["content"])
