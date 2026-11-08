"""Ranking parity, cache invalidation, scope isolation, and concurrency checks."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from evals.benchmark_lexical import benchmark, uncached_search
from local_preview.lexical_index import LexicalIndex


def tokenize(text, language=None):
    return text.lower().split()


@pytest.fixture
def rows():
    return [
        {"text": "alpha alpha beta", "language": "en", "video_id": "one", "start": 0},
        {"text": "beta gamma", "language": "en", "video_id": "two", "start": 10},
        {"text": "機械 学習 alpha", "language": "ja", "video_id": "three", "start": 20},
        {"text": "", "language": "en", "video_id": "four", "start": 30},
        {"text": "beta gamma", "language": "en", "video_id": "five", "start": 40},
    ]


def search(index, rows, query=None, version="v1"):
    return index.search(
        rows, query or ["alpha", "beta", "beta"], k=10, token_version=version
    )


def test_scores_order_and_citations_match_legacy_across_scopes(rows):
    index = LexicalIndex(tokenize)
    for scoped in (rows, rows[:2], rows[2:], list(reversed(rows)), rows):
        for query in (["alpha", "beta", "beta"], ["機械"], ["missing"]):
            expected = uncached_search(scoped, query, k=10, tokenize=tokenize)
            assert search(index, scoped, query) == expected
            assert search(index, scoped, query) == expected


def test_repeated_queries_and_new_scopes_reuse_document_tokens(rows):
    calls = []

    def counting_tokenize(text, language=None):
        calls.append((text, language))
        return tokenize(text, language)

    index = LexicalIndex(counting_tokenize)
    search(index, rows)
    count = len(calls)
    search(index, rows, ["gamma"])
    search(index, rows[:2])
    assert len(calls) == count
    rows[0]["text"] = "changed alpha"
    search(index, rows)
    assert len(calls) == count + 1


@pytest.mark.parametrize(
    "mutation", ["edit", "language", "delete", "add", "reorder", "metadata"]
)
def test_in_place_mutations_do_not_serve_stale_results(rows, mutation):
    def language_tokenize(text, language=None):
        return tokenize(text) if language == "en" else ["alpha"]

    index = LexicalIndex(language_tokenize)
    search(index, rows)
    if mutation == "edit":
        rows[0]["text"] = "gamma"
    elif mutation == "language":
        rows[1]["language"] = "ja"
    elif mutation == "delete":
        del rows[0]
    elif mutation == "add":
        rows.append({"text": "alpha", "language": "en", "video_id": "new"})
    elif mutation == "reorder":
        rows.reverse()
    else:
        rows[0].update(start=999, video_id="updated", title="new title")
    assert search(index, rows) == uncached_search(
        rows, ["alpha", "beta", "beta"], k=10, tokenize=language_tokenize
    )


def test_tokenizer_version_invalidates_statistics(rows):
    state = {"tokens": ["alpha"]}
    index = LexicalIndex(lambda *args, **kwargs: state["tokens"])
    assert search(index, rows, ["alpha"])
    state["tokens"] = ["different"]
    assert search(index, rows, ["alpha"], version="v2") == []


def test_eviction_and_clear_preserve_results(rows):
    index = LexicalIndex(tokenize, max_documents=2, max_scopes=1)
    expected = uncached_search(rows, ["alpha"], k=10, tokenize=tokenize)
    search(index, rows)
    search(index, rows[:1])
    assert len(index._documents) <= 2
    assert len(index._scopes) <= 1
    assert search(index, rows, ["alpha"]) == expected
    index.clear()
    assert not index._documents and not index._scopes
    assert search(index, rows, ["alpha"]) == expected


def test_concurrent_scopes_and_returned_row_mutation_are_isolated(rows):
    index = LexicalIndex(tokenize)
    scopes = [deepcopy(rows[:n]) for n in range(1, 6)] * 8
    with ThreadPoolExecutor(max_workers=8) as pool:
        actual = list(pool.map(lambda scope: search(index, scope), scopes))
    for scope, result in zip(scopes, actual):
        assert result == uncached_search(
            scope, ["alpha", "beta", "beta"], k=10, tokenize=tokenize
        )
    actual[-1][0]["score"] = -999
    assert search(index, rows)[0]["score"] > 0


def test_empty_inputs_and_invalid_limits(rows):
    index = LexicalIndex(tokenize)
    assert index.search(rows, [], k=5, token_version="v1") == []
    assert search(index, []) == []
    with pytest.raises(ValueError):
        LexicalIndex(tokenize, max_scopes=0)


def test_benchmark_checks_parity_without_latency_thresholds():
    report = benchmark(chunks=20, runs=1, tokenize=tokenize, token_version="test")
    assert report["exact_result_parity"] is True
    assert all(item["samples"] == 4 for item in report["metrics"].values())
