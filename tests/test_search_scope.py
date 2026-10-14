"""Search video scope and query-filtered feedback used by the Search workbench."""

import importlib
import sys
import threading
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
LOCAL_PREVIEW_DIR = ROOT_DIR / "local_preview"
for path in (ROOT_DIR, LOCAL_PREVIEW_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

local_api = importlib.import_module("local_api")


class _FakeHandler:
    def __init__(self, path, body):
        self.path = path
        self._body = body
        self.response_status = None
        self.response_payload = None

    def _read_json_body(self):
        return self._body

    def _json(self, payload, status=200):
        self.response_status = status
        self.response_payload = payload


class _RecordingService:
    def __init__(self):
        self.calls = []

    def retrieve(self, query, **kwargs):
        self.calls.append(("plain", kwargs))
        return {"retrieval_mode": "hybrid", "details": {}, "results": []}

    def retrieve_agentic(self, query, **kwargs):
        self.calls.append(("agentic", kwargs))
        return {"retrieval": {"retrieval_mode": "hybrid", "details": {}, "results": []}}


@pytest.fixture
def service(monkeypatch):
    stub = _RecordingService()
    monkeypatch.delenv(local_api.AGENTIC_RETRIEVAL_ENV, raising=False)
    monkeypatch.setattr(local_api, "SERVICE", stub)
    return stub


def _post(body):
    handler = _FakeHandler("/v1/search", body)
    local_api.Handler.do_POST(handler)
    return handler


def test_search_without_scope_searches_whole_library(service):
    handler = _post({"query": "作画"})

    assert handler.response_status == 200
    assert handler.response_payload["video_ids"] is None
    assert "video_ids" not in service.calls[0][1]


@pytest.mark.parametrize("agentic,kind", [(False, "plain"), (True, "agentic")])
def test_search_passes_deduplicated_video_scope(service, agentic, kind):
    handler = _post(
        {"query": "作画", "agentic": agentic, "video_ids": ["vidA", " vidB ", "vidA", ""]}
    )

    assert handler.response_status == 200
    assert handler.response_payload["video_ids"] == ["vidA", "vidB"]
    assert service.calls == [(kind, service.calls[0][1])]
    assert service.calls[0][1]["video_ids"] == ["vidA", "vidB"]


def test_search_keeps_an_explicit_empty_scope(service):
    handler = _post({"query": "作画", "video_ids": []})

    assert handler.response_status == 200
    assert service.calls[0][1]["video_ids"] == []


@pytest.mark.parametrize("value", ["vidA", [1, 2], {"vidA": True}])
def test_search_rejects_malformed_video_scope(service, value):
    handler = _post({"query": "作画", "video_ids": value})

    assert handler.response_status == 400
    assert service.calls == []


def test_search_rejects_oversized_video_scope(service):
    ids = [f"vid{index:08d}" for index in range(local_api.MAX_SEARCH_VIDEO_SCOPE + 1)]
    handler = _post({"query": "作画", "video_ids": ids})

    assert handler.response_status == 400
    assert service.calls == []


def test_retrieve_with_empty_scope_returns_no_results():
    real = local_api.LocalRAGService.__new__(local_api.LocalRAGService)
    real._normalize_reranker_mode = lambda value: "none"
    real._default_hybrid_profile = lambda: "baseline_rrf"

    retrieval = real.retrieve("作画", video_ids=[])

    assert retrieval["results"] == []
    assert retrieval["details"]["fallback"] == "empty_video_scope"


def _feedback_service():
    svc = local_api.LocalRAGService.__new__(local_api.LocalRAGService)
    svc.feedback = {}
    svc.feedback_index = {}
    svc.feedback_lock = threading.Lock()
    svc.feedback_tuning_enabled = True
    svc._persist_feedback = lambda: None
    return svc


def test_list_search_feedback_filters_by_normalized_query():
    svc = _feedback_service()
    for query, video_id in [("Anime  Direction", "vidA"), ("anime direction", "vidB"), ("baseball", "vidC")]:
        svc.save_search_feedback(
            {
                "query": query,
                "retrieval_mode": "hybrid",
                "label": "relevant",
                "video_id": video_id,
                "chunk_index": 0,
                "start": 0.0,
                "end": 10.0,
            }
        )

    rows = svc.list_search_feedback(query="  ANIME direction ")

    assert sorted(row["video_id"] for row in rows) == ["vidA", "vidB"]
    assert len(svc.list_search_feedback()) == 3


@pytest.mark.parametrize("agentic,kind", [(False, "plain"), (True, "agentic")])
def test_search_can_disable_feedback_tuning_per_request(service, agentic, kind):
    handler = _post({"query": "作画", "agentic": agentic, "feedback_tuning": False})

    assert handler.response_status == 200
    assert service.calls[0] == (kind, service.calls[0][1])
    assert service.calls[0][1]["feedback_tuning"] is False


def test_search_omits_feedback_tuning_by_default(service):
    _post({"query": "作画"})

    assert "feedback_tuning" not in service.calls[0][1]


def test_search_rejects_non_boolean_feedback_tuning(service):
    handler = _post({"query": "作画", "feedback_tuning": "no"})

    assert handler.response_status == 400
    assert service.calls == []


def test_feedback_rerank_override_ignores_labels():
    svc = _feedback_service()
    svc._infer_query_language = lambda query: "en"
    svc._tokenize_for_lexical = lambda text, language=None: text.split()
    svc._chunk_identity = lambda row: f"{row['video_id']}:{row['chunk_index']}"
    svc.save_search_feedback(
        {
            "query": "anime direction",
            "retrieval_mode": "hybrid",
            "label": "relevant",
            "video_id": "vidB",
            "chunk_index": 0,
            "start": 0.0,
            "end": 10.0,
        }
    )
    rows = [
        {"video_id": "vidA", "chunk_index": 0, "score": 0.5},
        {"video_id": "vidB", "chunk_index": 0, "score": 0.4},
    ]

    tuned = svc._apply_feedback_rerank(query="anime direction", rows=rows)
    untuned = svc._apply_feedback_rerank(query="anime direction", rows=rows, enabled=False)

    assert tuned["results"][0]["video_id"] == "vidB"
    assert [row["video_id"] for row in untuned["results"]] == ["vidA", "vidB"]
    assert untuned["adjusted_count"] == 0


def test_retrieval_tools_forward_feedback_tuning():
    from retrieval_tools import TranscriptRetrievalTools

    calls = []
    tools = TranscriptRetrievalTools(library=None, retrieve_fn=lambda query, **kwargs: calls.append(kwargs) or {})

    tools.semantic_search("q", feedback_tuning=False)
    tools.keyword_search("q")

    assert calls[0]["feedback_tuning"] is False
    assert "feedback_tuning" not in calls[1]
