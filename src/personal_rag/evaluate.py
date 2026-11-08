"""Retrieval + answer quality evaluation on a labelled question set.

Run inside the backend container so it can reach Ollama on the internal network:

    make eval            # retrieval metrics + answer check
    make eval-fast       # retrieval metrics only (no LLM generation)

Metrics (answerable questions only unless noted):
- hit@1 / hit@k: the expected source file is the top chunk / among the top-k chunks
- MRR: mean reciprocal rank of the first chunk from the expected source
- answer accuracy: generated answer contains at least one expected keyword
- abstention: for unanswerable questions, the model says it does not know
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .ingest import ingest
from .ollama import Ollama
from .rag import answer, retrieve
from .store import Hit, Store

ABSTAIN_MARKERS = ("don't know", "do not know", "not sure", "no information", "cannot", "can't")


@dataclass(frozen=True)
class RetrievalScore:
    n: int
    hit_at_1: float
    hit_at_k: float
    mrr: float


def score_retrieval(
    questions: list[dict[str, Any]], search: Callable[[str], list[Hit]]
) -> RetrievalScore:
    answerable = [q for q in questions if q.get("answerable", True)]
    h1 = hk = rr = 0.0
    for q in answerable:
        sources = [h.path for h in search(q["q"])]
        rank = next((i + 1 for i, s in enumerate(sources) if s == q["source"]), None)
        if rank is not None:
            hk += 1
            rr += 1 / rank
            h1 += rank == 1
    n = len(answerable)
    return RetrievalScore(n, h1 / n, hk / n, rr / n) if n else RetrievalScore(0, 0, 0, 0)


def keyword_hit(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def abstained(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in ABSTAIN_MARKERS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-answers", action="store_true", help="skip LLM generation")
    parser.add_argument(
        "--questions", default=os.environ.get("RAG_EVAL_FILE", "/eval/questions.json")
    )
    args = parser.parse_args()

    cfg = Settings.from_env()
    llm = Ollama(cfg.ollama_url, cfg.embed_model, cfg.chat_model)
    questions: list[dict[str, Any]] = json.loads(Path(args.questions).read_text())

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "eval.sqlite")
        st = ingest(cfg, store, llm)
        print(f"corpus: {st.indexed} files indexed, {len(st.failed)} failed\n")
        r = score_retrieval(questions, lambda q: retrieve(q, store, llm, cfg.top_k))
        print(f"embedding model : {cfg.embed_model}")
        print(f"chunk / overlap : {cfg.chunk_chars} / {cfg.chunk_overlap} chars, top_k={cfg.top_k}")
        print(f"answerable Qs   : {r.n}\n")
        print("| metric | value |\n|---|---|")
        print(f"| hit@1 | {r.hit_at_1:.0%} |")
        print(f"| hit@{cfg.top_k} | {r.hit_at_k:.0%} |")
        print(f"| MRR | {r.mrr:.2f} |")

        if args.no_answers:
            return
        correct = abstain_ok = n_ans = n_unans = 0
        print(f"\nchat model: {cfg.chat_model}")
        for q in questions:
            a = answer(q["q"], store, llm, cfg.top_k).text
            if q.get("answerable", True):
                n_ans += 1
                ok = keyword_hit(a, q["keywords"])
                correct += ok
            else:
                n_unans += 1
                ok = abstained(a)
                abstain_ok += ok
            print(f"  {'PASS' if ok else 'FAIL'}  {q['q']}")
        print("\n| metric | value |\n|---|---|")
        print(f"| answer accuracy (keyword) | {correct}/{n_ans} = {correct / n_ans:.0%} |")
        if n_unans:
            print(f"| correct abstention | {abstain_ok}/{n_unans} |")


if __name__ == "__main__":
    main()
