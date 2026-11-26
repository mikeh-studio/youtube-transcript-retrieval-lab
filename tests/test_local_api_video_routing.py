"""Ask-layer tests for video-first routing and YouTube provenance."""

import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from multilingual.video_routing import MultiVectorVideoRouter


ROOT_DIR = Path(__file__).resolve().parents[1]
LOCAL_PREVIEW_DIR = ROOT_DIR / "local_preview"
if str(LOCAL_PREVIEW_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_PREVIEW_DIR))

os.environ["YT_RAG_SKIP_GLOBAL_SERVICE"] = "1"
local_api = importlib.import_module("local_api")
grounded_answer = importlib.import_module("grounded_answer")


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


def _retrieval(video_ids, *, sufficient):
    rows = [
        {
            "video_id": video_id,
            "chunk_index": index,
            "text": f"evidence from {video_id}",
        }
        for index, video_id in enumerate(video_ids or [])
    ]
    return {
        "retrieval": {
            "retrieval_mode": "hybrid",
            "details": {},
            "results": rows,
        },
        "sufficient": sufficient,
    }


def test_video_first_agentic_expands_shortlist_before_global_fallback():
    service = local_api.LocalRAGService.__new__(local_api.LocalRAGService)
    service.engine = type(
        "Engine",
        (),
        {
            "library": type(
                "Library",
                (),
                {"videos": {video_id: {} for video_id in ("v1", "v2", "v3", "v4")}},
            )()
        },
    )()
    route_calls = []
    retrieval_scopes = []

    def route_videos(query, *, top_k, language, video_ids=None, rank_by_relevance=False):
        del video_ids
        route_calls.append((query, top_k, language))
        video_ids = ["v1", "v2"] if top_k == 2 else ["v1", "v2", "v3", "v4"]
        return {
            "video_ids": video_ids,
            "results": [{"video_id": value} for value in video_ids],
            "used_fallback": False,
            "fallback_reason": None,
            "dense_available": True,
            "lexical_available": True,
            "fusion": "rrf",
            "latency_ms": 1.5,
        }

    def retrieve_agentic(question, **kwargs):
        scope = kwargs["video_ids"]
        retrieval_scopes.append(scope)
        return _retrieval(scope, sufficient=len(scope) == 4)

    service.route_videos = route_videos
    service.retrieve_agentic = retrieve_agentic

    outcome = service.retrieve_video_first(
        "starter timing",
        video_top_k=2,
        agentic=True,
    )
    details = outcome["retrieval"]["details"]["video_routing"]
    assert [call[1] for call in route_calls] == [2, 4]
    assert retrieval_scopes == [["v1", "v2"], ["v1", "v2", "v3", "v4"]]
    assert [stage["scope"] for stage in details["stages"]] == [
        "routed_top_k",
        "routed_expanded",
    ]
    assert details["selected_video_ids"] == ["v1", "v2"]
    assert details["used_fallback"] is False


def _multilingual_service():
    service = local_api.LocalRAGService.__new__(local_api.LocalRAGService)
    service.engine = type(
        "Engine",
        (),
        {
            "library": type(
                "Library",
                (),
                {
                    "videos": {
                        "en_video": {"language": "en"},
                        "ja_video": {"language": "ja"},
                    }
                },
            )()
        },
    )()
    service._expand_query_for_video_languages = lambda *args, **kwargs: {
        "query_language": "en",
        "translation_applied": True,
        "translation_error": None,
        "variants": [
            {
                "language": "en",
                "query": "starter timing",
                "query_language": "en",
                "kind": "original",
                "video_ids": ["en_video"],
            },
            {
                "language": "ja",
                "query": "発酵のタイミング",
                "query_language": "ja",
                "kind": "translation",
                "video_ids": ["ja_video"],
            },
        ],
    }
    service._route_query_variants = lambda *args, **kwargs: {
        "video_ids": ["en_video", "ja_video"],
        "results": [],
        "used_fallback": False,
        "fallback_reason": None,
        "dense_available": True,
        "lexical_available": True,
        "fusion": "rrf",
        "latency_ms": 1.0,
        "route_attempts": [],
    }

    return service


def test_video_first_agentic_preserves_multilingual_tool_traces():
    service = _multilingual_service()

    def retrieve_agentic(query, **kwargs):
        language = kwargs["language"]
        tool = "keyword_search" if language == "ja" else "semantic_search"
        mode = "lexical" if language == "ja" else "dense"
        video_id = kwargs["video_ids"][0]
        return {
            "retrieval": {
                "retrieval_mode": mode,
                "details": {
                    "agentic_retrieval": {
                        "enabled": True,
                        "applied": False,
                        "sufficient": True,
                        "stopped_reason": "sufficient_evidence",
                        "final_query": query,
                        "final_retrieval_mode": mode,
                        "final_tool": tool,
                        "final_k": 5,
                        "policy": "deterministic_tool_policy_v1",
                        "attempts": [
                            {
                                "attempt": 1,
                                "strategy": "initial",
                                "tool": tool,
                                "query": query,
                                "retrieval_mode": mode,
                                "k": 5,
                                "result_count": 1,
                                "sufficient": True,
                                "reason_code": "strong_single_chunk",
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "video_id": video_id,
                        "chunk_index": 0,
                        "language": language,
                        "rank": 1,
                        "text": query,
                    }
                ],
            },
            "sufficient": True,
        }

    service.retrieve_agentic = retrieve_agentic

    outcome = service.retrieve_video_first(
        "starter timing",
        video_top_k=2,
        agentic=True,
    )
    trace = outcome["retrieval"]["details"]["agentic_retrieval"]

    assert trace["final_tool"] == "multilingual_fusion"
    assert [row["language"] for row in trace["language_outcomes"]] == [
        "en",
        "ja",
    ]
    assert [row["language"] for row in trace["attempts"][:-1]] == ["en", "ja"]
    assert trace["attempts"][-1]["tool"] == "multilingual_fusion"
    assert trace["attempts"][-1]["languages"] == ["en", "ja"]


def test_video_first_non_agentic_expands_when_routed_evidence_is_weak(
    monkeypatch,
):
    service = local_api.LocalRAGService.__new__(local_api.LocalRAGService)
    all_video_ids = ("v1", "v2", "v3", "v4")
    service.engine = type(
        "Engine",
        (),
        {
            "library": type(
                "Library",
                (),
                {"videos": {video_id: {} for video_id in all_video_ids}},
            )()
        },
    )()
    route_calls = []
    retrieval_scopes = []

    def route_videos(query, *, top_k, language, video_ids=None, rank_by_relevance=False):
        del query, language, video_ids
        route_calls.append(top_k)
        selected = ["v1", "v2"] if top_k == 2 else list(all_video_ids)
        return {
            "video_ids": selected,
            "results": [{"video_id": value} for value in selected],
            "used_fallback": False,
            "fallback_reason": None,
            "dense_available": True,
            "lexical_available": True,
            "fusion": "rrf",
            "latency_ms": 1.0,
        }

    def retrieve(question, **kwargs):
        del question
        scope = kwargs["video_ids"]
        retrieval_scopes.append(scope)
        return _retrieval(scope, sufficient=False)["retrieval"]

    monkeypatch.setattr(
        local_api,
        "assess_grounded_answer_evidence",
        lambda **kwargs: {
            "sufficient": len(kwargs["rows"]) >= 4,
            "reason_code": (
                "multi_chunk_support" if len(kwargs["rows"]) >= 4 else "thin_support"
            ),
        },
    )
    service.route_videos = route_videos
    service.retrieve = retrieve

    outcome = service.retrieve_video_first(
        "starter timing",
        video_top_k=2,
        agentic=False,
    )
    details = outcome["retrieval"]["details"]["video_routing"]

    assert route_calls == [2, 4]
    assert retrieval_scopes == [["v1", "v2"], ["v1", "v2", "v3", "v4"]]
    assert [stage["scope"] for stage in details["stages"]] == [
        "routed_top_k",
        "routed_expanded",
    ]
    assert [stage["sufficient"] for stage in details["stages"]] == [False, True]
    assert details["used_fallback"] is False


def test_video_first_non_agentic_falls_back_globally_after_weak_expansion(
    monkeypatch,
):
    service = local_api.LocalRAGService.__new__(local_api.LocalRAGService)
    all_video_ids = ["v1", "v2", "v3", "v4", "v5"]
    service.engine = type(
        "Engine",
        (),
        {
            "library": type(
                "Library",
                (),
                {"videos": {video_id: {} for video_id in all_video_ids}},
            )()
        },
    )()
    retrieval_scopes = []

    def route_videos(query, *, top_k, language, video_ids=None, rank_by_relevance=False):
        del query, language, video_ids
        selected = all_video_ids[:top_k]
        return {
            "video_ids": selected,
            "results": [{"video_id": value} for value in selected],
            "used_fallback": False,
            "fallback_reason": None,
            "dense_available": True,
            "lexical_available": True,
            "fusion": "rrf",
            "latency_ms": 1.0,
        }

    def retrieve(question, **kwargs):
        del question
        scope = kwargs["video_ids"]
        retrieval_scopes.append(scope)
        result_ids = all_video_ids if scope is None else scope
        return _retrieval(result_ids, sufficient=False)["retrieval"]

    monkeypatch.setattr(
        local_api,
        "assess_grounded_answer_evidence",
        lambda **kwargs: {
            "sufficient": len(kwargs["rows"]) >= 5,
            "reason_code": (
                "multi_chunk_support" if len(kwargs["rows"]) >= 5 else "thin_support"
            ),
        },
    )
    service.route_videos = route_videos
    service.retrieve = retrieve

    outcome = service.retrieve_video_first(
        "starter timing",
        video_top_k=2,
        agentic=False,
    )
    details = outcome["retrieval"]["details"]["video_routing"]

    assert retrieval_scopes == [
        ["v1", "v2"],
        ["v1", "v2", "v3", "v4"],
        None,
    ]
    assert [stage["scope"] for stage in details["stages"]] == [
        "routed_top_k",
        "routed_expanded",
        "global_fallback",
    ]
    assert details["used_fallback"] is True
    assert details["fallback_reason"] == "insufficient_routed_evidence"


def test_video_first_uses_global_retrieval_when_router_is_unavailable():
    service = local_api.LocalRAGService.__new__(local_api.LocalRAGService)
    service.engine = type(
        "Engine",
        (),
        {"library": type("Library", (), {"videos": {"v1": {}, "v2": {}}})()},
    )()
    service.route_videos = lambda *args, **kwargs: {
        "video_ids": [],
        "results": [],
        "used_fallback": True,
        "fallback_reason": "router_unavailable",
        "dense_available": False,
        "lexical_available": False,
        "fusion": "rrf",
        "latency_ms": 0.5,
    }
    scopes = []

    def retrieve(question, **kwargs):
        scopes.append(kwargs["video_ids"])
        return {
            "retrieval_mode": "hybrid",
            "details": {},
            "results": [],
        }

    service.retrieve = retrieve
    outcome = service.retrieve_video_first("question", agentic=False)
    details = outcome["retrieval"]["details"]["video_routing"]

    assert scopes == [None]
    assert details["used_fallback"] is True
    assert details["fallback_reason"] == "router_unavailable"
    assert details["stages"][0]["scope"] == "global_fallback"


@pytest.mark.parametrize(
    ("question_language", "relevant_language"),
    [("en", "en"), ("en", "ja"), ("ja", "en")],
)
def test_mixed_library_routes_to_relevant_videos(
    monkeypatch, question_language, relevant_language
):
    questions = {"en": "How do kites float?", "ja": "凧はなぜ浮きますか"}
    relevant_texts = {
        "en": ["Fabric wings catch a breeze.", "Tension keeps the sail stable."],
        "ja": ["布の翼が風を受ける。", "糸の張力で翼が安定する。"],
    }
    unrelated_texts = {
        "en": ["Clay bowls dry overnight.", "The kiln needs a vent."],
        "ja": ["粘土の器を一晩乾燥させる。", "窯には通気口が必要だ。"],
    }
    lexical_decoys = {"en": "Kites painted on a teapot.", "ja": "凧は茶器の絵柄。"}
    videos = {}
    dense_by_text = {}
    for language in questions:
        is_relevant = language == relevant_language
        texts = relevant_texts[language] if is_relevant else unrelated_texts[language]
        scores = [0.83 if is_relevant else 0.805] * 2 + [0.6]
        for index, (title, cosine) in enumerate(
            zip([*texts, lexical_decoys[language]], scores)
        ):
            videos[f"{language}-{index}"] = {"title": title, "language": language}
            dense_by_text[title] = cosine

    def embed_passages(passages):
        vectors = []
        for passage in passages:
            cosine = next(
                score
                for text, score in dense_by_text.items()
                if text in passage["embed_text"]
            )
            vectors.append([cosine, (1.0 - cosine**2) ** 0.5])
        return np.array(vectors, dtype="float32")

    processor = SimpleNamespace(
        generate_embeddings=embed_passages,
        encode_query=lambda *_args, **_kwargs: np.array([[1.0, 0.0]], dtype="float32"),
        embedding_metadata=lambda: {"backend": "test", "model": "test", "dim": 2},
    )
    router = MultiVectorVideoRouter(processor)
    router.build(videos, persist=False)
    service = local_api.LocalRAGService.__new__(local_api.LocalRAGService)
    service.engine = SimpleNamespace(library=SimpleNamespace(videos=videos))
    service._get_video_router = lambda: router
    question = questions[question_language]

    def translate(**kwargs):
        request = json.loads(kwargs["user_message"])
        assert request["query"] == question
        assert request["source_language"] == question_language
        (other_language,) = request["target_languages"]
        payload = {"translations": {other_language: questions[other_language]}}
        return {"text": json.dumps(payload)}

    service._llm_text_response = translate
    retrieval_calls = []

    def retrieve(query, **kwargs):
        retrieval_calls.append((query, kwargs["language"], kwargs["video_ids"]))
        return _retrieval(kwargs["video_ids"], sufficient=True)["retrieval"]

    service.retrieve = retrieve
    monkeypatch.setattr(
        local_api,
        "assess_grounded_answer_evidence",
        lambda **kwargs: {"sufficient": True, "reason_code": "fixture"},
    )
    output = service.retrieve_video_first(
        question, language=question_language, video_top_k=2
    )
    details = output["retrieval"]["details"]["video_routing"]
    selected = [f"{relevant_language}-0", f"{relevant_language}-1"]

    assert details["selected_video_ids"] == selected
    assert details["fusion"] == "language_pool_median"
    assert retrieval_calls == [(questions[relevant_language], relevant_language, selected)]
    for candidate in details["candidates"]:
        (hit,) = candidate["variant_hits"]
        assert hit["relevance_score"] == pytest.approx(0.83)
        # Both relevant sources sit at their pool's median; raw relevance breaks the tie.
        assert candidate["score"] == hit["pool_score"] == pytest.approx(0.0)
        assert hit["lexical_score"] is None
    # The lexical decoy would make the per-language RRF shortlist.
    assert all(
        attempt["video_ids"] == [f"{attempt['language']}-0", f"{attempt['language']}-1"]
        for attempt in details["route_attempts"]
    )


def test_english_frieren_query_routes_to_japanese_video_after_translation():
    service = local_api.LocalRAGService.__new__(local_api.LocalRAGService)
    service.engine = type(
        "Engine",
        (),
        {
            "library": type(
                "Library",
                (),
                {
                    "videos": {
                        "frieren1234": {"language": "ja"},
                        "nba12345678": {"language": "en"},
                    }
                },
            )()
        },
    )()
    service._llm_text_response = lambda **kwargs: {
        "provider": "chatgpt",
        "model": "test-model",
        "text": (
            '{"translations":{"ja":'
            '"フリーレンのポッドキャストについて詳しく教えて。何が話されましたか"}}'
        ),
    }

    def route_videos(query, *, top_k, language, video_ids=None, rank_by_relevance=False):
        del query, top_k
        if language == "ja":
            video_id = "frieren1234"
            lexical_score = 0.7
        else:
            video_id = "nba12345678"
            lexical_score = None
        assert video_id in video_ids
        return {
            "video_ids": [video_id],
            "results": [
                {
                    "video_id": video_id,
                    "title": video_id,
                    "language": language,
                    "source": {"platform": "youtube"},
                    "dense_score": 0.5,
                    "lexical_score": lexical_score,
                }
            ],
            "used_fallback": False,
            "fallback_reason": None,
            "latency_ms": 1.0,
        }

    retrieval_calls = []

    def retrieve(query, **kwargs):
        retrieval_calls.append((query, kwargs["language"], kwargs["video_ids"]))
        return {
            "retrieval_mode": "hybrid",
            "details": {},
            "results": [
                {
                    "video_id": kwargs["video_ids"][0],
                    "chunk_index": 0,
                    "language": kwargs["language"],
                    "rank": 1,
                    "dense_score": 0.9,
                    "lexical_score": 8.0,
                    "text": query,
                }
            ],
        }

    service.route_videos = route_videos
    service.retrieve = retrieve
    question = "Tell me more about the Frieren podcasts? What was discussed"

    outcome = service.retrieve_video_first(
        question,
        video_top_k=1,
        provider="chatgpt",
    )
    details = outcome["retrieval"]["details"]["video_routing"]
    grounding = outcome["retrieval"]["details"]["answer_grounding"]

    assert details["selected_video_ids"] == ["frieren1234"]
    assert details["query_expansion"]["translation_applied"] is True
    assert details["query_expansion"]["variants"][1]["language"] == "ja"
    assert grounding == {
        "question": "フリーレンのポッドキャストについて詳しく教えて。何が話されましたか",
        "query_language": "ja",
        "answer_language": "en",
    }
    assert retrieval_calls == [
        (
            "フリーレンのポッドキャストについて詳しく教えて。何が話されましたか",
            "ja",
            ["frieren1234"],
        )
    ]


def test_untranslated_fallback_keeps_source_query_language_for_grounding():
    service = local_api.LocalRAGService.__new__(local_api.LocalRAGService)
    service.engine = type(
        "Engine",
        (),
        {
            "library": type(
                "Library",
                (),
                {"videos": {"frieren1234": {"language": "ja"}}},
            )()
        },
    )()

    def unavailable_translation(**kwargs):
        raise RuntimeError("translation unavailable")

    service._llm_text_response = unavailable_translation
    service._tokenize_for_lexical = lambda text, language: (
        list(text) if language == "ja" else text.lower().split()
    )

    def route_videos(query, *, top_k, language, video_ids=None, rank_by_relevance=False):
        del query, top_k
        selected_ids = ["frieren1234"] if language == "ja" else []
        assert all(video_id in (video_ids or []) for video_id in selected_ids)
        return {
            "video_ids": selected_ids,
            "results": [
                {
                    "video_id": video_id,
                    "language": language,
                    "dense_score": 0.9,
                }
                for video_id in selected_ids
            ],
            "used_fallback": False,
            "fallback_reason": None,
            "dense_available": True,
            "lexical_available": False,
            "fusion": "rrf",
            "latency_ms": 1.0,
        }

    def retrieve(query, **kwargs):
        assert query == "What does the video say about loneliness?"
        assert kwargs["language"] == "ja"
        assert kwargs["video_ids"] == ["frieren1234"]
        return {
            "retrieval_mode": "dense",
            "details": {},
            "results": [
                {
                    "video_id": "frieren1234",
                    "chunk_index": 1,
                    "language": "ja",
                    "rank": 1,
                    "dense_score": 0.87,
                    "score": 0.87,
                    "text": "主人公の孤独について詳しく説明します。",
                },
                {
                    "video_id": "frieren1234",
                    "chunk_index": 2,
                    "language": "ja",
                    "rank": 2,
                    "dense_score": 0.85,
                    "score": 0.85,
                    "text": "登場人物たちが主人公の孤独をどう受け止めるか比較します。",
                },
            ],
        }

    service.route_videos = route_videos
    service.retrieve = retrieve

    outcome = service.retrieve_video_first(
        "What does the video say about loneliness?",
        video_top_k=1,
        retrieval_mode="dense",
        provider="chatgpt",
    )
    details = outcome["retrieval"]["details"]
    japanese_variant = details["video_routing"]["query_expansion"]["variants"][1]

    assert japanese_variant["kind"] == "untranslated_fallback"
    assert japanese_variant["language"] == "ja"
    assert japanese_variant["query_language"] == "en"
    assert details["video_routing"]["stages"][0]["sufficient"] is True
    assert details["answer_grounding"] == {
        "question": "What does the video say about loneliness?",
        "query_language": "en",
        "answer_language": "en",
    }


@pytest.mark.parametrize(
    "request_overrides, expected_k, expected_route",
    [
        pytest.param({}, 10, "video_first", id="multi-vector-default"),
        pytest.param({"k": 7}, 7, "video_first", id="explicit-k"),
        pytest.param({"k": 10, "top_k": 6}, 6, "video_first", id="top-k-precedence"),
        pytest.param({"k": 20}, 12, "video_first", id="explicit-k-clamped"),
        pytest.param({"video_id": "video123456"}, 5, "plain", id="single-video"),
        pytest.param({"video_routing": "none"}, 5, "plain", id="unrouted-default"),
    ],
)
def test_ask_route_uses_video_first_only_for_all_videos(
    monkeypatch, request_overrides, expected_k, expected_route
):
    calls = []

    class StubService:
        def retrieve_video_first(self, question, **kwargs):
            calls.append(("video_first", kwargs))
            return {
                "retrieval": {
                    "retrieval_mode": "hybrid",
                    "details": {
                        "video_routing": {
                            "enabled": True,
                            "selected_video_ids": ["v1", "v2"],
                        }
                    },
                    "results": [],
                },
                "agentic_outcome": None,
            }

        def retrieve(self, question, **kwargs):
            calls.append(("plain", kwargs))
            return {"retrieval_mode": "hybrid", "details": {}, "results": []}

        def save_ask_history(self, row):
            pass

        def ask_with_sources(self, question, sources, **kwargs):
            return {
                "status": "insufficient_evidence",
                "answer": "n/a",
                "confidence": "low",
                "citations": [],
                "retrieved_chunks": [],
                "warnings": [],
                "sources": [],
                "provider": "chatgpt",
                "model": "test-model",
            }

    monkeypatch.setattr(local_api, "SERVICE", StubService())
    handler = _FakeHandler(
        "/v1/ask",
        {
            "question": "Which video explains starters?",
            "video_routing": "multi_vector",
            "video_top_k": 4,
            "agentic": expected_route == "video_first",
            "provider": "chatgpt",
            **request_overrides,
        },
    )

    local_api.Handler.do_POST(handler)

    assert handler.response_status == 200
    assert len(calls) == 1
    route, kwargs = calls[0]
    assert route == expected_route
    assert kwargs["k"] == handler.response_payload["k"] == expected_k
    if route == "video_first":
        assert kwargs["video_top_k"] == 4
        assert kwargs["agentic"] is True
        assert kwargs["provider"] == "chatgpt"
        assert handler.response_payload["retrieval_details"]["video_routing"]["enabled"]
    else:
        assert kwargs["video_id"] == request_overrides.get("video_id")


def test_citations_keep_youtube_source_separate_from_source_type():
    source = {
        "platform": "youtube",
        "video_id": "video123456",
        "url": "https://www.youtube.com/watch?v=video123456",
        "channel": {
            "id": "UC-source",
            "name": "Source Channel",
            "url": "https://www.youtube.com/channel/UC-source",
        },
        "metadata_provider": "youtube_data_api",
        "fetched_at": "2026-07-23T00:00:00+00:00",
    }
    rows = [
        {
            "source_type": "transcript",
            "source": source,
            "video_id": "video123456",
            "video_title": "Starter Guide",
            "chunk_index": 2,
            "text": "Feed the starter after peak fermentation.",
            "start": 65.0,
            "end": 80.0,
        }
    ]

    citation = grounded_answer.build_citation_catalog(rows)[0]
    chunk = grounded_answer.build_retrieved_chunks_payload(rows)[0]
    _system, prompt = grounded_answer.build_grounded_answer_messages(
        question="When should I feed it?",
        citations=[citation],
        answer_language="en",
    )

    assert citation["source_type"] == "transcript"
    assert citation["source"]["platform"] == "youtube"
    assert citation["source"]["channel"]["id"] == "UC-source"
    assert chunk["source"]["channel"]["name"] == "Source Channel"
    assert "YouTube channel: Source Channel" in prompt


@pytest.mark.parametrize("agentic", [False, True])
@pytest.mark.parametrize(
    "english_status,english_rows,japanese_rows,expected",
    [
        ("error", False, True, "degraded"),
        ("error", False, False, "error"),
        ("ok", True, True, "ok"),
        ("no_matches", False, False, "no_matches"),
        ("degraded", True, True, "degraded"),
    ],
)
def test_multilingual_merge_preserves_stage_diagnostics(
    monkeypatch, agentic, english_status, english_rows, japanese_rows, expected
):
    service = _multilingual_service()
    monkeypatch.setattr(
        local_api,
        "assess_grounded_answer_evidence",
        lambda **kwargs: {
            "sufficient": bool(kwargs["rows"]),
            "reason_code": "fixture",
        },
    )
    original_details = {}

    def retrieve(query, **kwargs):
        english = kwargs["language"] == "en"
        has_rows = english_rows if english else japanese_rows
        failed = english_status in {"error", "degraded"} and english
        status = english_status if english else ("ok" if has_rows else "no_matches")
        rows = (
            [
                {
                    "video_id": kwargs["video_ids"][0],
                    "chunk_index": 0,
                    "text": query,
                    "language": kwargs["language"],
                }
            ]
            if has_rows
            else []
        )
        details = {
            "status": status,
            "duration_ms": 3.0 if english else 5.0,
            "stages": {
                "dense": {
                    "status": "error"
                    if failed
                    else ("ok" if has_rows else "no_matches"),
                    "duration_ms": 2.0 if english else 4.0,
                    "error_type": "RuntimeError" if failed else None,
                },
                "lexical": {
                    "status": "skipped",
                    "duration_ms": 0.0,
                    "error_type": None,
                },
            },
        }
        original_details[kwargs["language"]] = details
        return {"results": rows, "details": details, "retrieval_mode": "dense"}

    service.retrieve = retrieve

    def retrieve_agentic(query, **kwargs):
        response = retrieve(query, **kwargs)
        return {"retrieval": response, "sufficient": bool(response["results"])}

    service.retrieve_agentic = retrieve_agentic
    result = service.retrieve_video_first(
        "starter timing", video_top_k=2, agentic=agentic
    )
    details = result["retrieval"]["details"]
    assert details["status"] == expected
    assert details["duration_ms"] == 8.0
    assert details["stages"]["dense"]["duration_ms"] == 6.0
    assert details["stages"]["lexical"]["status"] == "skipped"
    per_language = details["multilingual_retrieval"]["language_details"]
    assert [part["language"] for part in per_language] == ["en", "ja"]
    assert per_language[0]["status"] == english_status
    if english_status in {"error", "degraded"}:
        assert details["stages"]["dense"]["status"] == "error"
        assert details["stages"]["dense"]["error_type"] == "RuntimeError"
        original_details["en"]["stages"]["dense"]["error_type"] = "changed"
        assert per_language[0]["stages"]["dense"]["error_type"] == "RuntimeError"


@pytest.mark.parametrize(
    "merge, expected",
    [
        ("rrf", ["ja_video:0", "en_video:0", "ja_video:1", "en_video:1"]),
        ("dense", ["en_video:0", "en_video:1", "ja_video:0", "ja_video:1"]),
        ("", ["en_video:0", "en_video:1", "ja_video:0", "ja_video:1"]),
    ],
)
def test_multilingual_chunk_merge_modes(monkeypatch, merge, expected):
    """Rank-only fusion interleaves languages; dense merge, the default, follows similarity."""
    service = _multilingual_service()
    monkeypatch.setenv(local_api.MULTILINGUAL_CHUNK_MERGE_ENV, merge)
    monkeypatch.setattr(
        local_api, "assess_grounded_answer_evidence",
        lambda **kwargs: {"sufficient": bool(kwargs["rows"]), "reason_code": "fixture"},
    )
    dense = {"en": [0.86, 0.85], "ja": [0.80, 0.79]}

    def retrieve(query, **kwargs):
        language = kwargs["language"]
        rows = [
            {"video_id": kwargs["video_ids"][0], "chunk_index": index, "text": query,
             "language": language, "dense_score": score}
            for index, score in enumerate(dense[language])
        ]
        details = {"status": "ok", "duration_ms": 1.0, "stages": {}}
        return {"results": rows, "details": details, "retrieval_mode": "hybrid"}

    service.retrieve = retrieve
    result = service.retrieve_video_first("starter timing", k=4, video_top_k=2, agentic=False)
    rows = result["retrieval"]["results"]
    order = [f"{row['video_id']}:{row['chunk_index']}" for row in rows]
    if merge == "rrf":
        assert sorted(order) == sorted(expected)
    else:
        assert order == expected
    assert result["retrieval"]["details"]["multilingual_retrieval"]["fusion"] == (merge or "dense")


def test_unknown_multilingual_chunk_merge_is_rejected(monkeypatch):
    service = _multilingual_service()
    monkeypatch.setenv(local_api.MULTILINGUAL_CHUNK_MERGE_ENV, "weighted")
    with pytest.raises(ValueError, match="chunk merge"):
        service.retrieve_video_first("starter timing", k=4, video_top_k=2, agentic=False)


def test_dense_chunk_merge_falls_back_to_rank_without_dense_scores(monkeypatch):
    """Lexical-only searches carry no dense score, so each language's top hit survives."""
    service = _multilingual_service()
    monkeypatch.delenv(local_api.MULTILINGUAL_CHUNK_MERGE_ENV, raising=False)
    monkeypatch.setattr(
        local_api, "assess_grounded_answer_evidence",
        lambda **kwargs: {"sufficient": bool(kwargs["rows"]), "reason_code": "fixture"},
    )
    lexical = {"en": [0.4, 0.3], "ja": [0.9, 0.8]}

    def retrieve(query, **kwargs):
        language = kwargs["language"]
        rows = [
            {"video_id": kwargs["video_ids"][0], "chunk_index": index, "text": query,
             "language": language, "dense_score": None, "lexical_score": score}
            for index, score in enumerate(lexical[language])
        ]
        details = {"status": "ok", "duration_ms": 1.0, "stages": {}}
        return {"results": rows, "details": details, "retrieval_mode": "lexical"}

    service.retrieve = retrieve
    result = service.retrieve_video_first("starter timing", k=2, video_top_k=2, agentic=False)
    order = sorted(f"{row['video_id']}:{row['chunk_index']}" for row in result["retrieval"]["results"])
    assert order == ["en_video:0", "ja_video:0"]
    fusion = result["retrieval"]["details"]["multilingual_retrieval"]
    assert fusion["fusion"] == "rrf"
    assert fusion["requested_fusion"] == "dense"
