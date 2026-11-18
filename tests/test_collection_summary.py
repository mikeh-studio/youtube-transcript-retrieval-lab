"""Offline checks for collection clustering, citation integrity, and HTTP wiring."""

import json
import socket
from types import SimpleNamespace

import pytest

from local_preview.collection_summary import CollectionSummaryError, build_collection_summary
from tests.test_eval_generator_api import _request, local_api
from tests.test_local_api_summary_and_retrieval import _make_service


ROUTE = "/v1/summaries/collection"
IDS = ["lid-demo-a", "lid-demo-b", "lid-demo-c"]


def _section(title, start=0, end=10):
    return {"title": title, "summary": f"Notes about {title}.", "start": start, "end": end}


def _retrieved(source_id, start=3.5, end=5):
    return {
        "video_id": source_id, "start": start, "end": end,
        "text": "A cork lid rests on the jar.",
    }


def _perspective(source_id, refs, stance="adds"):
    return {
        "source_id": source_id, "stance": stance, "citations": refs,
        "statement": "The jar can use a cork lid.",
    }


def _theme(*perspectives):
    return {"title": "Lids", "summary": "A jar lid can be made of cork.",
            "perspectives": list(perspectives)}


def _build(source_ids=IDS[:2], **overrides):
    return build_collection_summary(source_ids, **{
        "language": "en", "focus": "Materials", "provider": "chatgpt", "model": "fixture",
        "key_areas_fn": lambda **_: {"sections": [_section("Cork")]},
        "embed_fn": lambda queries: [[1, 0] for _ in queries],
        "retrieve_fn": lambda **_: {"results": [_retrieved(IDS[0]), _retrieved(IDS[1])]},
        "llm": lambda **_: {"text": json.dumps({
            "overview": "Cork can cover a jar.",
            "themes": [_theme(_perspective(IDS[0], ["E1"]))],
        })},
        **overrides,
    })


@pytest.mark.parametrize("ids", [[], ["a"], ["a", " a "], "ab", ["a", 2], list(map(str, range(13)))])
def test_scope_is_validated_before_calls(ids):
    def unexpected(**kwargs):
        pytest.fail(f"Invalid scope called dependency: {kwargs}")

    with pytest.raises(ValueError, match="2 to 12 distinct"):
        _build(ids, key_areas_fn=unexpected)


def test_citations_are_resolved_by_theme_and_declared_source():
    def retrieve(**request):
        assert request["video_ids"] == IDS[:2]
        assert request["k"] == 8 and request["retrieval_mode"] == "hybrid"
        if request["query"].startswith("A"):
            return {"results": [
                _retrieved("other"), _retrieved(IDS[0]), _retrieved(IDS[1], 8, 12),
                _retrieved(IDS[1], -10, 12), _retrieved(IDS[1], 9, 8),
            ]}
        return {"results": [_retrieved(IDS[0], 20, 23)]}

    calls = []

    def llm(**request):
        calls.append(request)
        prompt = json.loads(request["user_message"])
        assert prompt["focus"] == "Materials"
        assert [[row["id"] for row in entry["evidence"]] for entry in prompt["candidates"]] == [
            ["E1", "E2"], ["E3"],
        ]
        return {"text": json.dumps({
            "overview": "The demos used jars.",
            "themes": [
                _theme(
                    _perspective(IDS[0], ["E1", "E2", "E3", "E99", {}]),
                    _perspective(IDS[1], ["E2"], "agrees"),
                    _perspective("other", ["E1"]),
                    _perspective(IDS[1], ["E2"], "unknown"),
                ),
                _theme(_perspective(IDS[0], ["E1"])),
            ],
        })}

    result = _build(
        key_areas_fn=lambda **_: {"sections": [_section("A"), _section("B")]},
        embed_fn=lambda _: [[1, 0], [0, 1], [1, 0], [0, 1]],
        retrieve_fn=retrieve,
        llm=llm,
    )
    assert len(calls) == 1
    assert result["themes"] == [_theme(
        _perspective(IDS[0], ["E1"]), _perspective(IDS[1], ["E2"], "agrees"),
    )]
    assert result["coverage"] == {
        "sources_selected": 2, "sources_cited": 2, "citation_validity": 2 / 9,
    }
    catalog = {row["citation_id"]: row for row in result["evidence_catalog"]}
    assert set(catalog) == {"E1", "E2", "E3"}
    assert catalog["E1"]["url"] == f"https://www.youtube.com/watch?v={IDS[0]}&t=3s"
    assert catalog["E1"]["start_seconds"] == 3.5 and catalog["E1"]["end_seconds"] == 5


def test_greedy_clustering_is_stable_and_ranks_sources_then_duration():
    source_sections = {
        IDS[0]: [_section("Shared"), _section("Long solo", 20, 200)],
        IDS[1]: [_section("At cutoff"), _section("Medium solo", 30, 80)],
        IDS[2]: [_section("Below cutoff")],
    }
    vectors = {
        "Shared": [2, 0, 0, 0], "At cutoff": [0.8, 0.6, 0, 0],
        "Below cutoff": [0.79, 0.61, 0, 0], "Long solo": [0, 0, 0, 1],
        "Medium solo": [0, 0, 1, 0],
    }

    def key_areas(*, source_id, **_):
        return {"sections": source_sections[source_id]}

    def embed(queries):
        return [vectors[query.split(" Notes about ")[0]] for query in queries]

    def run(ids):
        queries = []

        def retrieve(**request):
            queries.append(request["query"])
            return {"results": [_retrieved(IDS[0])]}

        def llm(**request):
            candidates = json.loads(request["user_message"])["candidates"]
            assert [[row["title"] for row in theme["sections"]] for theme in candidates] == [
                ["Shared", "At cutoff"], ["Long solo"], ["Medium solo"],
            ]
            return {"text": json.dumps({"overview": "", "themes": [_theme()] * 3})}

        _build(ids, key_areas_fn=key_areas, embed_fn=embed, retrieve_fn=retrieve,
               llm=llm, max_themes=3)
        return queries

    expected = [f"{title} Notes about {title}." for title in ("Shared", "Long solo", "Medium solo")]
    assert run(IDS) == run(IDS[::-1]) == expected


def test_no_evidence_skips_synthesis_and_bad_json_is_an_error():
    def unexpected(**_):
        pytest.fail("Synthesis should not be called for an empty catalog")

    result = _build(retrieve_fn=lambda **_: {"results": [_retrieved("other")]}, llm=unexpected)
    assert result["overview"] == ""
    assert result["themes"] == result["evidence_catalog"] == []
    assert result["coverage"]["sources_cited"] == 0
    with pytest.raises(CollectionSummaryError, match="invalid collection JSON"):
        _build(llm=lambda **_: {"text": "invalid"})
    with pytest.raises(CollectionSummaryError, match="one entry per collection theme"):
        _build(llm=lambda **_: {"text": '{"overview":"","themes":[]}'})


def test_collection_route_uses_cached_key_areas_and_processor(monkeypatch):
    calls = []
    service = _make_service()

    def embed(chunks):
        assert len(chunks) == 2
        assert all(row["embed_text"].startswith("Jar lids ") for row in chunks)
        return [[1, 0], [1, 0]]

    service.engine = SimpleNamespace(library=SimpleNamespace(
        processor=SimpleNamespace(generate_embeddings=embed),
        videos={
            source_id: {
                "title": f"Jar demo {source_id}",
                "full_transcript": {"segments": [
                    {"start": 3.5, "end": 7, "text": "The lid is shaped from soft cork."},
                ]},
            }
            for source_id in IDS[:2]
        },
    ))
    service.retrieve = lambda **_: {"results": [_retrieved(source_id) for source_id in IDS[:2]]}

    def llm(**request):
        calls.append(request)
        if '"candidates"' in request["user_message"]:
            payload = {"overview": "Both demos use cork.", "themes": [
                _theme(_perspective(IDS[0], ["E1"]), _perspective(IDS[1], ["E2"], "agrees")),
            ]}
        else:
            payload = {"overview": {"tldr": "Cork fits the jar."}, "sections": [{
                "title": "Jar lids", "summary": "Shaping cork.", "start_line": 1,
                "end_line": 1, "key_points": [],
            }]}
        return {"text": json.dumps(payload)}

    service._llm_text_response = llm
    monkeypatch.setattr(local_api, "SERVICE", service)
    request = {"source_ids": IDS[:2], "language": "en", "focus": "Lids",
               "provider": "chatgpt", "model": "gpt-5.4-mini"}
    assert _request("POST", ROUTE, {**request, "source_ids": [IDS[0], "other"]})[0] == 404
    assert _request("POST", ROUTE, {**request, "language": "bad"})[0] == 400
    assert _request("POST", ROUTE, {**request, "source_ids": [IDS[0]]})[0] == 400
    assert _request("POST", ROUTE, {**request, "focus": {}})[0] == 400
    assert calls == []
    for expected_call_count in (3, 4):
        status, _, body = _request("POST", ROUTE, request)
        payload = json.loads(body)
        assert status == 200 and payload["ok"], payload
        assert payload["model"] == request["model"]
        assert payload["coverage"]["sources_cited"] == 2
        assert len(calls) == expected_call_count

    client, server = socket.socketpair()
    with client, server:
        client.sendall(
            (f"POST {ROUTE} HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n"
             "Origin: http://127.0.0.1:8000\r\nContent-Type: application/json\r\n"
             "Content-Length: 2\r\n\r\n{}").encode("ascii")
        )
        client.shutdown(socket.SHUT_WR)
        local_api.Handler(server, ("127.0.0.1", 50000),
                          SimpleNamespace(server_address=("127.0.0.1", 8000)))
        assert client.recv(4096).startswith(b"HTTP/1.0 403")
    assert len(calls) == 4
    service._llm_text_response = lambda **_: {"text": "invalid"}
    status, _, body = _request("POST", ROUTE, request)
    assert status == 502 and json.loads(body)["error"]["code"] == "COLLECTION_SUMMARY_FAILED"
