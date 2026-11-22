"""Exercise Codex draft schemas, source scoping, and reviewed dataset rows."""

import json
from pathlib import Path

import pytest

from local_preview.label_drafts import LabelDraftError, LabelDraftService
from tests.test_label_drafts import wait_for_job


IDS = ["cork-lesson", "clay-lesson", "wax-lesson"]
ROOT = Path(__file__).resolve().parents[1]


def _draft(task, tmp_path, monkeypatch, proposal):
    monkeypatch.delenv("YT_RAG_LABEL_DRAFTER", raising=False)
    monkeypatch.delenv("YT_RAG_LABEL_DRAFTER_MODEL", raising=False)
    calls = []

    def drafter(**kwargs):
        calls.append(kwargs)
        return {"text": json.dumps(proposal), "provider": kwargs["provider"], "model": None}

    videos = {
        source_id: {
            "title": source_id,
            "duration_seconds": 45,
            "full_transcript": {
                "segments": [{"start": 2 * index, "end": 2 * index + 1, "text": line}],
            },
        }
        for index, (source_id, line) in enumerate(zip(
            IDS, ["Cork absorbs drops.", "Clay has a groove.", "Wax dots mark the handle."],
        ), start=1)
    }
    service = LabelDraftService(ROOT, tmp_path / "runtime", tmp_path / "eval", videos.get,
                                drafter, lambda: "test-time")
    with pytest.raises(LabelDraftError, match="distinct sources"):
        service.start_job(task, IDS[:1], "en")
    job = service.start_job(task, IDS, "en")
    result = wait_for_job(service, job["job_id"])
    assert result["status"] == "completed", result
    assert len(calls) == 1
    call = calls[0]
    assert call["provider"] == "codex_cli" and call["model"] is None
    assert call["schema_path"].is_file() and task in call["schema_path"].name
    assert all(f"BEGIN TRANSCRIPT {source_id}" in call["user_message"] for source_id in IDS)
    return service, service.get_draft(result["draft_id"]), videos


def _mark_and_finalize(service, draft, edits):
    draft = service.save_review(draft["draft_id"], [
        {"id": draft["items"][0]["id"], "decision": "edited", "edits": edits},
    ])
    manifest = service.finalize(draft["draft_id"])
    path = service.labels_dir / draft["task"] / f'{manifest["dataset_id"]}.jsonl'
    label = json.loads(path.read_text(encoding="utf-8"))
    assert label["selected_source_ids"] == IDS
    assert label["source_fingerprints"] == manifest["source_fingerprints"]
    assert label["label_provenance"]["drafter"] == "codex_cli"
    assert label["review_decision"] == "edited"
    assert path.stat().st_mode & 0o777 == 0o600
    return label


def test_multi_source_qa_resolves_lines_and_retains_reviewed_spans(tmp_path, monkeypatch):
    def ref(source_id, line=1):
        return {"source_id": source_id, "start_line": line, "end_line": line}

    valid = {
        "question": "How do both jar lids handle water?",
        "required_facts": ["Cork absorbs drops.", "Clay has a groove."],
        "gold_spans": [ref(IDS[0]), ref(IDS[1])],
    }
    service, draft, videos = _draft("multi_source_qa", tmp_path, monkeypatch, {"cases": [
        {**valid, "gold_spans": [ref(IDS[0]), ref(IDS[0])]},
        {**valid, "gold_spans": [ref(IDS[0]), ref("outside")]},
        {**valid, "gold_spans": [ref(IDS[0]), ref(IDS[1], 99)]},
        valid,
    ], "notes": ""})
    item, = draft["items"]
    assert item["gold_spans"] == [
        {"source_id": IDS[0], "start": 2, "end": 3},
        {"source_id": IDS[1], "start": 4, "end": 5},
    ]
    groups = service.context(draft["draft_id"], item["id"])["groups"]
    assert [group["source_id"] for group in groups] == IDS[:2]
    assert [group["lines"][0]["start"] for group in groups] == [2, 4]
    with pytest.raises(LabelDraftError, match="Gold span timestamps"):
        service.save_review(draft["draft_id"], [
            {"id": item["id"], "decision": "edited", "edits": {"gold_spans": [
                *item["gold_spans"], {"source_id": IDS[0], "start": 0, "end": 90},
            ]}},
        ])
    label = _mark_and_finalize(service, draft, {"question": "How do the lids manage drops?"})
    assert label["gold_spans"] == item["gold_spans"]
    assert label["question"] == "How do the lids manage drops?"
    videos[IDS[1]]["full_transcript"]["segments"][0]["text"] = "The lid is made of pebble."
    with pytest.raises(LabelDraftError, match="changed since drafting"):
        service.context(draft["draft_id"], item["id"])


def test_multi_source_qa_rejects_spans_on_omitted_sample_lines(tmp_path, monkeypatch):
    monkeypatch.setattr("local_preview.label_drafts.MAX_CONTEXT_CHARS", 200)
    calls = []

    def drafter(**kwargs):
        calls.append(kwargs)
        return {
            "text": json.dumps({
                "cases": [
                    {
                        "question": "Why does an omitted line fail validation?",
                        "required_facts": ["Omitted line fact."],
                        "gold_spans": [
                            {"source_id": IDS[0], "start_line": 2, "end_line": 2},
                            {"source_id": IDS[1], "start_line": 1, "end_line": 1},
                        ],
                    },
                    {
                        "question": "How do the shown lines compare?",
                        "required_facts": ["Cork absorbs drops.", "Clay has a groove."],
                        "gold_spans": [
                            {"source_id": IDS[0], "start_line": 1, "end_line": 3},
                            {"source_id": IDS[1], "start_line": 1, "end_line": 1},
                        ],
                    },
                ],
                "notes": "",
            }),
            "provider": kwargs["provider"],
            "model": None,
        }

    videos = {
        IDS[0]: {
            "title": IDS[0],
            "duration_seconds": 45,
            "full_transcript": {
                "segments": [
                    {"start": 0, "end": 5, "text": "Cork absorbs drops at the rim."},
                    {"start": 10, "end": 15, "text": "This middle line is omitted from the sample."},
                    {"start": 20, "end": 25, "text": "Press the cork lid firmly."},
                ],
            },
        },
        IDS[1]: {
            "title": IDS[1],
            "duration_seconds": 45,
            "full_transcript": {
                "segments": [{"start": 4, "end": 5, "text": "Clay has a groove."}],
            },
        },
    }
    service = LabelDraftService(
        ROOT, tmp_path / "runtime", tmp_path / "eval", videos.get, drafter, lambda: "test-time"
    )
    job = service.start_job("multi_source_qa", IDS[:2], "en")
    result = wait_for_job(service, job["job_id"])
    assert result["status"] == "completed", result
    assert "[0002]" not in calls[0]["user_message"]
    draft = service.get_draft(result["draft_id"])
    assert [row["question"] for row in draft["items"]] == ["How do the shown lines compare?"]


def test_collection_themes_validate_membership_through_review(tmp_path, monkeypatch):
    service, draft, _ = _draft("collection_themes", tmp_path, monkeypatch, {"themes": [
        {"title": "Jar lids", "source_ids": [IDS[0], "outside"]},
        {"title": "Lid materials", "source_ids": IDS[:2]},
    ], "notes": ""})
    item, = draft["items"]
    assert item["source_ids"] == IDS[:2]
    with pytest.raises(LabelDraftError, match="distinct sources"):
        service.save_review(draft["draft_id"], [
            {"id": item["id"], "decision": "edited", "edits": {"source_ids": ["outside"]}},
        ])
    label = _mark_and_finalize(service, draft, {"source_ids": [IDS[0]]})
    assert label["source_ids"] == [IDS[0]]
    groups = service.context(draft["draft_id"], item["id"])["groups"]
    assert [group["source_id"] for group in groups] == IDS
