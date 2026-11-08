"""Tests for the optimized hybrid retrieval profile."""

import importlib
import os
import sys
import threading
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
LOCAL_PREVIEW_DIR = ROOT_DIR / "local_preview"
if str(LOCAL_PREVIEW_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_PREVIEW_DIR))

os.environ["YT_RAG_SKIP_GLOBAL_SERVICE"] = "1"
local_api = importlib.import_module("local_api")
LocalRAGService = local_api.LocalRAGService


def _make_service():
    service = LocalRAGService.__new__(LocalRAGService)
    service.feedback = {}
    service.feedback_index = {}
    service.feedback_lock = threading.Lock()
    service.feedback_tuning_enabled = False
    service._persist_feedback = lambda: None
    service.engine = type(
        "DummyEngine",
        (),
        {
            "library": type(
                "DummyLibrary",
                (),
                {
                    "videos": {
                        "vid1": {
                            "chunks": [
                                {"raw_text": "AI 推論 の一般論", "start": 0, "end": 10},
                                {"raw_text": "別の話題", "start": 10, "end": 20},
                                {"raw_text": "さらに別の話題", "start": 20, "end": 30},
                                {
                                    "raw_text": "Jetson Orin は エッジAI 推論 に使う",
                                    "start": 30,
                                    "end": 40,
                                },
                            ]
                        }
                    }
                },
            )()
        },
    )()
    dense_rows = [
        {
            "video_id": "vid1",
            "chunk_index": 0,
            "text": "AI 推論 の一般論",
            "score": 0.95,
            "dense_score": 0.95,
            "start": 0,
            "end": 10,
            "rank": 1,
        },
        {
            "video_id": "vid1",
            "chunk_index": 1,
            "text": "別の話題",
            "score": 0.80,
            "dense_score": 0.80,
            "start": 10,
            "end": 20,
            "rank": 2,
        },
        {
            "video_id": "vid1",
            "chunk_index": 2,
            "text": "さらに別の話題",
            "score": 0.75,
            "dense_score": 0.75,
            "start": 20,
            "end": 30,
            "rank": 3,
        },
        {
            "video_id": "vid1",
            "chunk_index": 3,
            "text": "Jetson Orin は エッジAI 推論 に使う",
            "score": 0.60,
            "dense_score": 0.60,
            "start": 30,
            "end": 40,
            "rank": 4,
        },
    ]
    lexical_rows = [
        {
            "video_id": "vid1",
            "chunk_index": 3,
            "text": "Jetson Orin は エッジAI 推論 に使う",
            "score": 8.0,
            "lexical_score": 8.0,
            "start": 30,
            "end": 40,
            "rank": 1,
        },
        {
            "video_id": "vid1",
            "chunk_index": 0,
            "text": "AI 推論 の一般論",
            "score": 4.0,
            "lexical_score": 4.0,
            "start": 0,
            "end": 10,
            "rank": 2,
        },
    ]
    service._dense_search = lambda query, k, language, video_id=None: dense_rows
    service._lexical_bm25_search = lambda query, k, language, video_id=None: (
        lexical_rows
    )
    return service


def test_optimized_hybrid_profile_fixes_rrf_dense_tie_bias():
    service = _make_service()

    baseline = service.retrieve(
        "Jetson Orin エッジAI 推論",
        k=1,
        retrieval_mode="hybrid",
        retrieval_profile="baseline_rrf",
    )
    optimized = service.retrieve(
        "Jetson Orin エッジAI 推論",
        k=1,
        retrieval_mode="hybrid",
        retrieval_profile="optimized_v1",
    )

    assert baseline["results"][0]["chunk_index"] == 0
    assert optimized["results"][0]["chunk_index"] == 3
    assert optimized["details"]["fusion_profile"] == "optimized_v1"
    assert (
        optimized["details"]["fusion_weights"]["dense"]
        == optimized["details"]["fusion_weights"]["lexical"]
    )


def test_hybrid_default_remains_baseline_rrf(monkeypatch):
    monkeypatch.delenv(local_api.HYBRID_PROFILE_ENV, raising=False)
    service = _make_service()

    result = service.retrieve(
        "Jetson Orin エッジAI 推論",
        k=1,
        retrieval_mode="hybrid",
    )

    assert result["results"][0]["chunk_index"] == 0
    assert result["details"]["fusion"] == "rrf"
    assert result["details"]["fusion_profile"] == "baseline_rrf"


def test_hybrid_default_can_be_switched_by_env(monkeypatch):
    monkeypatch.setenv(local_api.HYBRID_PROFILE_ENV, "optimized_v1")
    service = _make_service()

    result = service.retrieve(
        "Jetson Orin エッジAI 推論",
        k=1,
        retrieval_mode="hybrid",
    )

    assert result["results"][0]["chunk_index"] == 3
    assert result["details"]["fusion"] == "weighted_normalized"
    assert result["details"]["fusion_profile"] == "optimized_v1"


def test_dense_failure_is_reported_without_discarding_lexical_results():
    service = _make_service()
    # Exercise the actual dense wrapper, which previously swallowed errors.
    service._dense_search = LocalRAGService._dense_search.__get__(service)

    def broken_search(*args, **kwargs):
        raise RuntimeError("private provider configuration")

    service.engine.search = broken_search
    response = service.retrieve("Jetson", retrieval_mode="hybrid")
    details = response["details"]
    assert response["results"]
    assert details["status"] == "degraded"
    assert details["fallback"] == "lexical_only"
    assert details["stages"]["dense"]["status"] == "error"
    assert details["stages"]["dense"]["error_type"] == "RuntimeError"
    assert details["stages"]["lexical"]["status"] == "ok"
    assert "private provider" not in str(response)

    failed = service.retrieve("Jetson", retrieval_mode="dense")
    assert failed["results"] == []
    assert failed["details"]["status"] == "error"
    assert failed["details"]["stages"]["lexical"]["status"] == "skipped"


def test_no_matches_is_distinct_from_failure_and_timings_are_present():
    service = _make_service()
    service._dense_search = lambda *args, **kwargs: []
    service._lexical_bm25_search = lambda *args, **kwargs: []
    response = service.retrieve("missing")
    details = response["details"]
    assert details["status"] == "no_matches"
    assert details["duration_ms"] >= 0
    for stage in details["stages"].values():
        assert stage["status"] == "no_matches"
        assert stage["error_type"] is None
        assert stage["duration_ms"] >= 0
    empty = service.retrieve("missing", video_ids=[])
    assert empty["details"]["status"] == "no_matches"
    assert all(
        stage["status"] == "skipped" for stage in empty["details"]["stages"].values()
    )


def test_stage_errors_do_not_leak_between_concurrent_requests():
    from concurrent.futures import ThreadPoolExecutor

    service = _make_service()
    good_dense = service._dense_search

    def sometimes_broken(query, **kwargs):
        if query == "broken":
            raise ValueError("failed query")
        return good_dense(query, **kwargs)

    service._dense_search = sometimes_broken
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(service.retrieve, ["broken", "good"] * 10))
    for index, response in enumerate(responses):
        expected = "degraded" if index % 2 == 0 else "ok"
        assert response["details"]["status"] == expected


def test_lexical_failure_preserves_dense_fallback():
    service = _make_service()

    def broken_lexical(*args, **kwargs):
        raise RuntimeError("tokenizer failed")

    service._lexical_bm25_search = broken_lexical
    response = service.retrieve("Jetson")
    assert response["results"]
    assert response["details"]["status"] == "degraded"
    assert response["details"]["fallback"] == "dense_only"


def test_real_lexical_path_reuses_tokens_and_refreshes_metadata():
    service = _make_service()
    service._lexical_bm25_search = LocalRAGService._lexical_bm25_search.__get__(service)
    calls = []

    def counting_tokenizer(text, language=None):
        calls.append(text)
        return text.lower().split()

    service._tokenize_for_lexical = counting_tokenizer
    first = service.retrieve("Jetson", retrieval_mode="lexical")
    initial_calls = len(calls)
    service.engine.library.videos["vid1"]["title"] = "Updated title"
    second = service.retrieve("Jetson", retrieval_mode="lexical")
    # Query tokenization also occurs in feedback assessment; documents must
    # not be tokenized again anywhere in the warm retrieval path.
    assert calls[initial_calls:]
    assert set(calls[initial_calls:]) == {"Jetson"}
    assert first["results"][0]["score"] == second["results"][0]["score"]
    assert second["results"][0]["video_title"] == "Updated title"
    service.engine.library.videos["vid1"]["chunks"][3]["raw_text"] = "different content"
    assert service.retrieve("Jetson", retrieval_mode="lexical")["results"] == []


def test_both_search_stages_failing_is_error_not_no_matches():
    service = _make_service()

    def broken(*args, **kwargs):
        raise RuntimeError("private credentials must not be exposed")

    service._dense_search = broken
    service._lexical_bm25_search = broken
    response = service.retrieve("Jetson")
    assert response["results"] == []
    assert response["details"]["status"] == "error"
    assert all(
        stage["status"] == "error" for stage in response["details"]["stages"].values()
    )
    assert "private credentials" not in str(response)
