"""Offline checks for line-grounded key areas and the local summary route."""

import json
import re
import socket
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from local_preview.key_areas import KeyAreasGenerationError, build_key_areas
from local_preview.transcript_lines import fingerprint_lines, numbered_lines
from tests.test_eval_generator_api import _request, local_api
from tests.test_local_api_summary_and_retrieval import _make_service


SOURCE_ID = "demoDrum001"
ROUTE = "/v1/summaries/key-areas"


def _segments(texts):
    return [
        {"start": 3.5 + 40 * index, "end": 25.75 + 40 * index, "text": text}
        for index, text in enumerate(texts)
    ]


def _point(refs, speakers=()):
    return {
        "text": "The rehearsal changed.",
        "evidence_lines": refs,
        "speakers": list(speakers),
    }


def _section(start, end, *points):
    return {
        "title": f"Rehearsal {start}",
        "summary": "The team worked through the setup.",
        "start_line": start,
        "end_line": end,
        "key_points": list(points),
    }


def _draft(*sections):
    return {"overview": {"tldr": "The team rehearsed."}, "sections": list(sections)}


class FakeLLM:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        payload = next(self.responses)
        return {
            "text": payload if isinstance(payload, str) else json.dumps(payload),
            "provider": kwargs["provider"],
            "model": kwargs["model"],
            "cli_version": "fixture-cli",
        }


def _build(segments, llm, **kwargs):
    return build_key_areas(
        segments,
        **{
            "language": "en",
            "source_meta": {"source_id": SOURCE_ID},
            "participants": [],
            "provider": "codex_cli",
            "model": "fixture-model",
            "llm": llm,
            **kwargs,
        },
    )


def test_repairs_section_boundaries_merges_and_validates_citations():
    segments = _segments(
        [
            "Mira Bell: Use the small drum.",
            "Leave a beat of silence.",
            ">> Use a soft mallet.",
            "An extra mallet is in the closet.",
            "Ivo Reed Fox: Try the echo.",
            "It sounds fine from the last row.",
            "Some chairs are by the door.",
            "We will start at noon.",
        ]
    )
    llm = FakeLLM(
        _draft(
            _section(5, 6, _point([5, 7], ["Ivo Reed Fox"])),
            _section(-3, 2, _point([1, 2, 2, 5, False, 0, "1"])),
            _section(2, 4, _point([3, 5], ["Mira Bell"]), _point([7])),
            _section(3, 3, _point([3])),
            _section(8, 99),
        )
    )
    result = _build(segments, llm)
    assert result["overview"] == {"tldr": "The team rehearsed."}
    first, second = result["sections"]
    assert [
        (row["start_line"], row["end_line"], row["start"], row["end"])
        for row in (first, second)
    ] == [(1, 4, 3.5, 145.75), (5, 8, 163.5, 305.75)]
    point, next_point = first["key_points"]
    assert point["evidence_lines"] == [1, 2]
    assert point["evidence"] == [
        {"start": 3.5, "end": 25.75},
        {"start": 43.5, "end": 65.75},
    ]
    assert (point["start"], point["end"], point["url"]) == (
        3.5,
        65.75,
        f"https://www.youtube.com/watch?v={SOURCE_ID}&t=3s",
    )
    assert next_point["evidence_lines"] == [3] and next_point["speakers"] == []
    assert second["key_points"][0]["speakers"] == ["Ivo Reed Fox"]
    assert result["quality"] == {
        "citation_validity": 6 / 13,
        "section_coverage": 1.0,
        "repaired_boundaries": 7,
        "dropped_key_points": 2,
        "anchor_mode": "line_ids",
    }


def test_extends_ends_and_enforces_section_count_with_caption_overlap():
    segments = _segments(["Set up.", "Check.", "Try.", "Repeat.", "Begin.", "Wrap up."])
    segments[1]["end"] = 148
    result = _build(
        segments,
        FakeLLM(_draft(_section(2, 2), _section(3, 3), _section(5, 5))),
        max_sections=2,
        min_section_seconds=0,
    )
    first, second = result["sections"]
    assert (first["start_line"], first["end_line"], first["end"]) == (1, 2, 148)
    assert (second["start_line"], second["end_line"]) == (3, 6)


def test_windowing_keeps_global_ids_and_uses_one_merge_call():
    segments = _segments(["La " * 1000 for _ in range(6)])
    llm = FakeLLM(
        _draft(_section(1, 2)),
        _draft(_section(3, 4)),
        _draft(_section(5, 6)),
        _draft(_section(1, 3, _point([3])), _section(4, 6, _point([5, 6]))),
    )
    result = _build(segments, llm)
    assert len(llm.calls) == 4
    for index, call in enumerate(llm.calls[:3]):
        window = (
            call["user_message"]
            .split("BEGIN TRANSCRIPT\n")[1]
            .split("\nEND TRANSCRIPT")[0]
        )
        assert len(window) <= 7000
        assert re.findall(r"^\[(\d+)\]", window, re.M) == [
            f"{index * 2 + 1:04d}",
            f"{index * 2 + 2:04d}",
        ]
    merge = llm.calls[-1]["user_message"]
    assert "BEGIN WINDOW DRAFTS" in merge
    assert '"start_line": 5' in merge
    assert "BEGIN TRANSCRIPT" not in merge
    assert result["generation_details"] == {
        "cache_hit": False,
        "windows": 3,
        "retries": 0,
    }
    assert [
        point["evidence_lines"]
        for section in result["sections"]
        for point in section["key_points"]
    ] == [[3], [5, 6]]


def test_parse_retry_budget_and_strict_json():
    segments = _segments(["A blue block steadied the drum."])
    response = _draft(_section(1, 1, _point([1])))
    llm = FakeLLM("not JSON", '["wrong shape"]', response)
    assert _build(segments, llm)["generation_details"]["retries"] == 2
    assert len(llm.calls) == 3
    llm = FakeLLM("not JSON", '["wrong shape"]', "NaN")
    with pytest.raises(KeyAreasGenerationError, match="invalid key-area JSON"):
        _build(segments, llm)
    assert len(llm.calls) == 3


def test_windowing_preserves_all_text_from_an_oversized_line():
    segments = _segments(["Tap " * 4300 + "Finish with a soft drum."])
    draft = _draft(_section(1, 1, _point([1])))
    llm = FakeLLM(draft, draft, draft, draft)
    result = _build(segments, llm)
    windows = [
        call["user_message"].split("BEGIN TRANSCRIPT\n")[1].split("\nEND TRANSCRIPT")[0]
        for call in llm.calls[:-1]
    ]
    assert len(windows) == 3
    assert all(
        window.startswith("[0001] 0:03 ") and len(window) <= 7000 for window in windows
    )
    assert "".join(window.split(" ", 2)[2] for window in windows) == segments[0]["text"]
    assert result["sections"][0]["key_points"][0]["evidence_lines"] == [1]


def test_speaker_attribution_is_limited_to_the_cited_line_or_its_turn():
    segments = _segments(
        [
            "Mira Bell: Try the drum.",
            "The front row heard a soft echo.",
            ">> Ivo Reed Fox moved the chair.",
            "The front row has space.",
            "Mira Bells checked the clock.",
        ]
    )
    llm = FakeLLM(
        _draft(
            _section(
                1,
                5,
                _point([2], ["mira bell", "Mira Bell", "Ivo Reed Fox"]),
                _point([3], ["Ivo Reed Fox"]),
                _point([4, 5], ["Ivo Reed Fox", "Mira Bell"]),
            )
        )
    )
    result = _build(segments, llm)
    points = result["sections"][0]["key_points"]
    assert [point["speakers"] for point in points] == [
        ["mira bell"],
        ["Ivo Reed Fox"],
        [],
    ]


@pytest.fixture
def service(tmp_path):
    service = _make_service()
    service.cache_data_dir = tmp_path
    segments = _segments(
        [
            "（青木はな）小さい太鼓を使います。",
            "次は少し休みましょう。",
            "＞＞ 道具を片づけます。",
            "最後に椅子を戻します。",
        ]
    )
    source = {
        "title": "練習会 出演：青木はな、森川れん",
        "full_transcript": {"segments": segments},
    }
    service.engine = SimpleNamespace(
        library=SimpleNamespace(videos={SOURCE_ID: source})
    )
    service._resolve_llm_model = lambda provider, model: model or "fixture-model"
    return service, source


def test_ja_cast_requires_transcript_evidence_and_cache_is_fingerprinted(service):
    service, source = service
    proposal = _draft(
        _section(
            1,
            4,
            _point([2], ["青木はな", "森川れん"]),
            _point([3, 4], ["青木はな", "森川れん"]),
        )
    )
    llm = FakeLLM(proposal, proposal, proposal, proposal, proposal)
    service._llm_text_response = llm
    args = (SOURCE_ID, "ja", "codex_cli", None)
    first = service.generate_key_areas(*args)
    assert "森川れん" in llm.calls[0]["user_message"].split("BEGIN TRANSCRIPT")[0]
    assert [point["speakers"] for point in first["sections"][0]["key_points"]] == [
        ["青木はな"],
        [],
    ]
    assert first["generation_details"]["cache_hit"] is False
    assert first["source_fingerprint"] == fingerprint_lines(
        numbered_lines(source["full_transcript"]["segments"])
    )

    cached = service.generate_key_areas(*args)
    assert cached["cached"] is cached["generation_details"]["cache_hit"] is True
    assert cached["sections"] == first["sections"]
    assert first["cached"] is False
    assert len(llm.calls) == 1

    path = service.cache_data_dir / "key_areas" / f"{SOURCE_ID}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (
        service.generate_key_areas(SOURCE_ID, "en", "codex_cli", None)["cached"]
        is False
    )
    assert (
        service.generate_key_areas(SOURCE_ID, "ja", "claude_cli", None)["cached"]
        is False
    )
    assert (
        service.generate_key_areas(SOURCE_ID, "ja", "codex_cli", "other")["cached"]
        is False
    )
    assert service.generate_key_areas(*args)["cached"] is True

    source["full_transcript"]["segments"][0]["text"] += " よく聞こえます。"
    assert service.generate_key_areas(*args)["cached"] is False
    assert len(llm.calls) == 5


def test_key_areas_http_aliases_errors_and_csrf(monkeypatch, service):
    service, source = service
    service._llm_text_response = FakeLLM(_draft(_section(1, 4)), *["invalid"] * 3)
    monkeypatch.setattr(local_api, "SERVICE", service)
    for alias in ("source_id", "video_id"):
        status, _, body = _request("POST", ROUTE, {alias: SOURCE_ID, "language": "ja"})
        payload = json.loads(body)
        assert status == 200 and payload["ok"] and payload["source_id"] == SOURCE_ID
        assert payload["cached"] is (alias == "video_id")
    assert _request("POST", ROUTE, {"source_id": "missing"})[0] == 404
    assert _request("POST", ROUTE, {"source_id": SOURCE_ID, "language": "fr"})[0] == 400
    status, _, body = _request(
        "POST", ROUTE, {"source_id": SOURCE_ID, "language": "en"}
    )
    assert (
        status == 502
        and json.loads(body)["error"]["code"] == "KEY_AREAS_GENERATION_FAILED"
    )
    source["full_transcript"] = {"text": "The room was quiet."}
    for _ in range(2):
        assert _request("POST", ROUTE, {"source_id": SOURCE_ID})[0] == 400

    client, server = socket.socketpair()
    with client, server:
        client.sendall(
            (
                f"POST {ROUTE} HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n"
                "Origin: http://127.0.0.1:8000\r\nContent-Type: application/json\r\n"
                "Content-Length: 2\r\n\r\n{}"
            ).encode("ascii")
        )
        client.shutdown(socket.SHUT_WR)
        local_api.Handler(
            server,
            ("127.0.0.1", 50000),
            SimpleNamespace(server_address=("127.0.0.1", 8000)),
        )
        assert client.recv(4096).startswith(b"HTTP/1.0 403")


def test_legacy_backfill_requires_actual_chunk_timestamps(service):
    service, source = service
    source["full_transcript"] = {"text": "Untimed notes."}
    source["chunks"] = [
        {"raw_text": "A wooden stand.", "start": "unknown", "end": 5},
        {
            "raw_text": "A felt pad protected the stand.",
            "start": 5.25,
            "timestamp_end": 12,
        },
    ]
    service._llm_text_response = FakeLLM(_draft(_section(1, 1, _point([1]))))
    result = service.generate_key_areas(SOURCE_ID)
    section = result["sections"][0]
    assert (section["start"], section["end"]) == (5.25, 12)
    assert section["key_points"][0]["evidence"] == [{"start": 5.25, "end": 12}]


def test_concurrent_cache_writes_preserve_distinct_keys_and_the_first_result(service):
    service, _ = service
    barrier = threading.Barrier(3)

    def llm(**kwargs):
        draft = _draft(_section(1, 4))
        draft["overview"]["tldr"] = threading.current_thread().name
        barrier.wait(timeout=5)
        return {"text": json.dumps(draft)}

    service._llm_text_response = llm
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(service.generate_key_areas, SOURCE_ID, language)
            for language in ("ja", "ja", "en")
        ]
        first, second, english = [future.result(timeout=10) for future in futures]
    assert first["overview"] == second["overview"]
    assert {first["cached"], second["cached"]} == {True, False}
    for result in (first, english):
        hit = service.generate_key_areas(SOURCE_ID, result["language"])
        assert hit["overview"] == result["overview"] and hit["cached"]
