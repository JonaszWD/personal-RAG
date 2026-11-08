from personal_rag.evaluate import abstained, keyword_hit, score_retrieval
from personal_rag.store import Hit

QS = [
    {"q": "a", "source": "x.md", "keywords": ["k"]},
    {"q": "b", "source": "y.md", "keywords": ["k"]},
    {"q": "c", "source": "z.md", "keywords": ["k"]},
    {"q": "unanswerable", "answerable": False},
]


def fake_search(q: str) -> list[Hit]:
    table = {
        "a": ["x.md", "y.md"],  # rank 1
        "b": ["x.md", "y.md"],  # rank 2
        "c": ["x.md", "y.md"],  # miss
    }
    return [Hit(p, "", 1.0) for p in table[q]]


def test_score_retrieval_metrics() -> None:
    s = score_retrieval(QS, fake_search)
    assert s.n == 3
    assert s.hit_at_1 == 1 / 3 and s.hit_at_k == 2 / 3
    assert abs(s.mrr - (1 + 0.5 + 0) / 3) < 1e-9


def test_keyword_and_abstention_helpers() -> None:
    assert keyword_hit("It is 50,000 yen", ["50000", "50,000"])
    assert not keyword_hit("no idea", ["cinnamon"])
    assert abstained("I don't know.") and not abstained("The answer is 7.")
