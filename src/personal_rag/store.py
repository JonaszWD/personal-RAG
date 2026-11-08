"""SQLite chunk store with brute-force cosine search (fine for PoC scale)."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class Hit:
    path: str
    text: str
    score: float


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        # One shared connection used from request threads and the ingest thread:
        # serialize every access, or concurrent use corrupts cursor state.
        self._lock = threading.RLock()
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                text TEXT NOT NULL,
                embedding BLOB NOT NULL
            );
            """
        )
        self._db.execute("PRAGMA foreign_keys = ON")

    def file_hash(self, path: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT sha256 FROM files WHERE path = ?", (path,)).fetchone()
        return None if row is None else str(row[0])

    def known_paths(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT path FROM files")}

    def replace_file(
        self, path: str, sha256: str, chunks: list[str], embeddings: list[list[float]]
    ) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM files WHERE path = ?", (path,))
            self._db.execute("INSERT INTO files(path, sha256) VALUES (?, ?)", (path, sha256))
            self._db.executemany(
                "INSERT INTO chunks(path, text, embedding) VALUES (?, ?, ?)",
                [
                    (path, t, np.asarray(e, dtype=np.float32).tobytes())
                    for t, e in zip(chunks, embeddings, strict=True)
                ],
            )

    def delete_file(self, path: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM files WHERE path = ?", (path,))

    def search(self, query_vec: list[float], k: int) -> list[Hit]:
        with self._lock:
            rows = self._db.execute("SELECT path, text, embedding FROM chunks").fetchall()
        if not rows:
            return []
        matrix = np.vstack([np.frombuffer(r[2], dtype=np.float32) for r in rows])
        q = np.asarray(query_vec, dtype=np.float32)
        denom = np.linalg.norm(matrix, axis=1) * np.linalg.norm(q)
        scores = matrix @ q / np.where(denom == 0, 1.0, denom)
        top = np.argsort(scores)[::-1][:k]
        return [Hit(rows[i][0], rows[i][1], float(scores[i])) for i in top]

    def chunk_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT path, COUNT(*) FROM chunks GROUP BY path").fetchall()
        return {str(p): int(n) for p, n in rows}

    def stats(self) -> tuple[int, int]:
        with self._lock:
            files = self._db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            chunks = self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return int(files), int(chunks)
