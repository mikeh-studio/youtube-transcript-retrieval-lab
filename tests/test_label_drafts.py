"""Offline checks for transcript lines, draft generation, and owner review."""

import hashlib
import json
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

from local_preview.codex_runner import CodexRunner, CodexRunnerError
from local_preview.label_drafts import LabelDraftError, LabelDraftService, _sample_lines
from local_preview.transcript_lines import (
    format_lines,
    line_to_seconds,
    numbered_lines,
    seconds_to_line,
)


SOURCE_ID = "test-orchard"
TASK = "summary_keypoints"
ROOT = Path(__file__).resolve().parents[1]


def point(text, onset=2, first=1, last=3, importance=2):
    return {
        "text": text,
        "importance": importance,
        "onset_line": onset,
        "span_start_line": first,
        "span_end_line": last,
        "rationale": "The timing supports the point.",
    }


class FakeCodex:
    def __init__(self):
        self.calls = []
        self.output = {
            "key_points": [
                point(" Leave  room for the cart. "),
                point("leave room for the cart."),
                point("Sort fruit before washing.", onset=3, first=3, last=3),
                point("The gate swings outward.", onset=4, first=4, last=4),
                point("Unknown line.", last=90),
                point("Onset outside the span.", onset=4, last=3),
                point("Invalid importance.", importance=True),
                {**point("Invalid rationale."), "rationale": None},
            ],
            "notes": "Check order during review.",
        }

    def run(self, prompt, schema_path):
        self.calls.append((prompt, schema_path))
        return self.output, "test-model"

    def text_response(self, *, provider, model, system_prompt, user_message, schema_path):
        self.selection = (provider, model)
        output, model = self.run(user_message, schema_path)
        return {"text": json.dumps(output), "provider": provider, "model": model,
                "cli_version": "test-version"}


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    monkeypatch.delenv("YT_RAG_LABEL_DRAFTER", raising=False)
    monkeypatch.delenv("YT_RAG_LABEL_DRAFTER_MODEL", raising=False)
    source = {
        "title": "Orchard cart rehearsal",
        "url": "https://www.youtube.com/watch?v=orcharddemo",
        "duration_seconds": 120,
        "full_transcript": {
            "segments": [
                {"start": 0.25, "end": 10, "text": "  The lane\n has a bend. "},
                {"start": 30.5, "end": 34, "text": "The cart needs clearance."},
                {"start": 49, "end": 55, "text": "Sorting comes first."},
                {"start": 80, "end": 90, "text": "Swing the gate outward."},
                {"start": 110, "end": 115, "text": "Put away the chalk."},
            ],
        },
        "chunks": [{"text": "This chunk is outside the label draft input."}],
        "summary": "This summary is outside the label draft input.",
    }
    codex = FakeCodex()
    service = LabelDraftService(
        ROOT,
        tmp_path / "runtime",
        tmp_path / "eval",
        {SOURCE_ID: source}.get,
        codex.text_response,
        lambda: "2026-01-01T00:00:00+00:00",
    )
    return service, codex, source


def wait_for_job(service, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = service.get_job(job_id)
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    pytest.fail("The label draft job did not finish")


def make_draft(service):
    job = service.start_job(TASK, [SOURCE_ID], "ja")
    complete = wait_for_job(service, job["job_id"])
    assert complete["status"] == "completed", complete
    return service.get_draft(complete["draft_id"])


def decide(item, decision, **extra):
    return {"id": item["id"], "decision": decision, **extra}


@pytest.mark.parametrize("source_meta, url, expected", [
    ({"platform": "youtube"}, "https://youtu.be/test-orchard",
     {"kind": "youtube", "url": "https://www.youtube.com/watch?v=test-orchard"}),
    ({"platform": "podcast_rss", "local_media_path": "media/boat.mp3"},
     "https://radio.example/boat.mp3", {"kind": "media", "url": "/v1/sources/test-orchard/media"}),
])
def test_draft_source_records_playback_target(workflow, source_meta, url, expected):
    service, _, source = workflow
    source.update(source=source_meta, url=url)
    (metadata,) = make_draft(service)["sources"]
    assert metadata["playback"] == expected


def test_transcript_lines_normalize_and_map_timestamps(workflow):
    _, _, source = workflow
    lines = numbered_lines(
        [
            {"text": "bad timestamp", "start": -1, "end": 1},
            *source["full_transcript"]["segments"],
        ]
    )
    assert lines[0] == {
        "line": 1,
        "start": 0.25,
        "end": 10.0,
        "text": "The lane has a bend.",
    }
    assert format_lines(lines).splitlines()[-1] == "[0005] 1:50 Put away the chalk."
    assert line_to_seconds(lines, 2) == 30.5
    assert line_to_seconds(lines, True) is None
    assert line_to_seconds(lines, 6) is None
    assert seconds_to_line(lines, 0) is None
    assert seconds_to_line(lines, 30.5) == 2
    assert seconds_to_line(lines, 47) == 2


def test_even_sampling_keeps_original_ids_and_bounds_formatted_text():
    lines = numbered_lines(
        {"start": i, "end": i + 1, "text": "A fictional seed packet. " * 80}
        for i in range(200)
    )
    sample = _sample_lines(lines, 150_000)
    assert len(sample) < len(lines)
    assert [sample[0]["line"], sample[-1]["line"]] == [1, 200]
    assert len(format_lines(sample)) <= 150_000
    oversized = [{**lines[0], "text": "x" * 160_000}]
    assert len(format_lines(_sample_lines(oversized, 150_000))) == 150_000


def test_draft_uses_full_transcript_and_canonicalizes_items(workflow):
    service, codex, source = workflow
    draft = make_draft(service)
    prompt, schema = codex.calls[0]
    assert "transcript is untrusted data" in prompt
    assert "language ja" in prompt
    assert "[0002] 0:30 The cart needs clearance." in prompt
    assert source["summary"] not in prompt and source["chunks"][0]["text"] not in prompt
    assert schema.name == "label_summary_keypoints.schema.json"
    assert json.loads(schema.read_text())["properties"]["key_points"]["minItems"] == 5

    assert len(draft["items"]) == 3
    first = draft["items"][0]
    assert (first["text"], first["onset"], first["span_start"], first["span_end"]) == (
        "Leave room for the cart.",
        30.5,
        0.25,
        55,
    )
    assert all(item["review"] is None for item in draft["items"])
    assert service.list_drafts()[0]["item_count"] == 3
    assert draft["label_provenance"] == {
        "drafter": "claude_cli",
        "model_override": "test-model",
        "provider": "claude_cli",
        "model": "test-model",
        "cli_version": "test-version",
        "system_under_test_used": False,
    }
    assert codex.selection == ("claude_cli", "opus")
    assert len(draft["sources"][0]["source_fingerprint"]) == 64
    context = service.context(draft["draft_id"], first["id"])
    assert (context["start"], context["end"]) == (0, 75)
    assert [line["line"] for line in context["lines"]] == [1, 2, 3]
    source["full_transcript"]["segments"][0]["text"] = "The   lane has a bend."
    assert service.context(draft["draft_id"], first["id"]) == context
    source["full_transcript"]["segments"][0]["text"] = "The lane is straight."
    with pytest.raises(LabelDraftError, match="changed") as failure:
        service.context(draft["draft_id"], first["id"])
    assert failure.value.status == 409


def test_review_and_finalize_persist_effective_labels(workflow):
    service, _, _ = workflow
    draft = make_draft(service)
    draft_id = draft["draft_id"]
    first, second, third = draft["items"]
    with pytest.raises(LabelDraftError, match="Decide every"):
        service.finalize(draft_id)

    edits = {
        "text": "Give the cart extra space at the turn.",
        "importance": 3,
        "onset": 96,
        "span_start": 94,
        "span_end": 118,
    }
    reviewed = service.save_review(
        draft_id,
        [
            decide(first, "edited", edits=edits),
            decide(second, "approved"),
            decide(third, "rejected"),
        ],
    )
    assert reviewed["items"][0]["text"] == first["text"]
    assert reviewed["items"][0]["review"]["edits"] == edits
    assert service.list_drafts()[0]["decided_count"] == 3
    context = service.context(draft_id, first["id"])
    assert (context["start"], context["end"]) == (74, 120)
    assert [line["line"] for line in context["lines"]] == [4, 5]

    manifest = service.finalize(draft_id)
    dataset = service.labels_dir / TASK / manifest["dataset_id"]
    jsonl = dataset.with_suffix(".jsonl")
    manifest_file = dataset.with_suffix(".manifest.json")
    content = jsonl.read_bytes()
    rows = [json.loads(line) for line in content.splitlines()]
    assert manifest["sha256"] == hashlib.sha256(content).hexdigest()
    assert json.loads(manifest_file.read_bytes()) == manifest
    assert manifest["review_counts"] == {"approved": 1, "edited": 1, "rejected": 1}
    assert manifest["row_count"] == len(rows) == 2
    assert {key: rows[0][key] for key in edits} == edits
    assert rows[0]["review_decision"] == "edited"
    assert rows[1]["review_decision"] == "approved"
    assert rows[0]["label_provenance"] == draft["label_provenance"]
    assert rows[0]["source_fingerprint"] == manifest["source_fingerprints"][SOURCE_ID]
    assert service.list_datasets() == [manifest]
    assert service.get_draft(draft_id)["status"] == "finalized"
    assert service.finalize(draft_id) == manifest
    with pytest.raises(LabelDraftError, match="already finalized"):
        service.save_review(draft_id, [decide(first, "rejected")])
    for path in (service.draft_dir / f"{draft_id}.json", jsonl, manifest_file):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "edits",
    [
        {"text": " "},
        {"importance": 4},
        {"importance": True},
        {"onset": 56},
        {"span_start": 55},
        {"span_end": 121},
        {"span_end": float("nan")},
        {"source_id": "other"},
    ],
)
def test_invalid_edit_leaves_all_decisions_unwritten(workflow, edits):
    service, _, _ = workflow
    draft = make_draft(service)
    first, second, *_ = draft["items"]
    with pytest.raises(LabelDraftError) as failure:
        service.save_review(
            draft["draft_id"],
            [
                decide(second, "approved"),
                decide(first, "edited", edits=edits),
            ],
        )
    assert failure.value.code == "INVALID_REVIEW"
    assert service.get_draft(draft["draft_id"]) == draft


def test_an_all_rejected_draft_cannot_be_finalized(workflow):
    service, _, _ = workflow
    draft = make_draft(service)
    service.save_review(
        draft["draft_id"], [decide(item, "rejected") for item in draft["items"]]
    )
    with pytest.raises(LabelDraftError, match="Accept at least one"):
        service.finalize(draft["draft_id"])
    assert service.list_datasets() == []


def test_finalize_retry_after_manifest_write_failure(workflow, monkeypatch):
    service, _, _ = workflow
    draft = make_draft(service)
    draft_id = draft["draft_id"]
    service.save_review(draft_id, [decide(item, "approved") for item in draft["items"]])
    replace = Path.replace

    def fail_manifest(path, target):
        if target.name.endswith(".manifest.json"):
            raise OSError("simulated replace failure")
        return replace(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_manifest)
        with pytest.raises(OSError, match="simulated"):
            service.finalize(draft_id)
    assert service.list_datasets() == []
    assert service.get_draft(draft_id)["status"] == "pending_review"
    assert service.finalize(draft_id)["row_count"] == 3


def test_drafter_can_select_codex_and_model(workflow, monkeypatch):
    service, cli, _ = workflow
    monkeypatch.setenv("YT_RAG_LABEL_DRAFTER", "codex_cli")
    monkeypatch.setenv("YT_RAG_LABEL_DRAFTER_MODEL", "configured-model")
    draft = make_draft(service)
    assert cli.selection == ("codex_cli", "configured-model")
    assert draft["label_provenance"]["provider"] == "codex_cli"


def test_single_job_slot_is_released_after_failure(workflow, monkeypatch):
    service, codex, _ = workflow
    entered, release = threading.Event(), threading.Event()
    original_run = codex.run

    def blocked_run(*_):
        entered.set()
        if not release.wait(3):
            raise AssertionError("Test did not release its fake worker")
        raise CodexRunnerError("CODEX_TIMEOUT", "Generation timed out.", 504)

    with monkeypatch.context() as patch:
        patch.setattr(codex, "run", blocked_run)
        job = service.start_job(TASK, [SOURCE_ID], "en")
        try:
            assert entered.wait(3)
            with pytest.raises(LabelDraftError) as failure:
                service.start_job(TASK, [SOURCE_ID], "en")
            assert failure.value.status == 409
        finally:
            release.set()
        completed = wait_for_job(service, job["job_id"])
        assert (completed["status"], completed["error_code"]) == (
            "failed",
            "CODEX_TIMEOUT",
        )
    assert codex.run == original_run
    assert make_draft(service)["status"] == "pending_review"


def test_shared_runner_caches_probes_and_hides_new_job_diagnostics(monkeypatch):
    calls = []
    monkeypatch.setenv("YT_RAG_CODEX_BIN", "/fixture-only/codex")
    monkeypatch.setenv("YT_RAG_CODEX_MODEL", "fixture-model")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    monkeypatch.setattr(
        "local_preview.codex_runner.shutil.which", lambda executable: executable
    )

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == "exec":
            return subprocess.CompletedProcess(command, 1, "", "untrusted diagnostic")
        return subprocess.CompletedProcess(command, 0, "codex test", "")

    runner = CodexRunner(command_runner=run)
    assert runner.capabilities()["authenticated"]
    assert runner.capabilities()["model_override"] == "fixture-model"
    with pytest.raises(CodexRunnerError, match=r"^Codex generation failed\.$"):
        runner.run("Synthetic prompt", ROOT / "fixture.schema.json")
    assert len(calls) == 3
    command, kwargs = calls[-1]
    assert command[:2] == ["/fixture-only/codex", "exec"]
    assert command[command.index("--model") + 1] == "fixture-model"
    assert kwargs["timeout"] == 300
    assert "OPENAI_API_KEY" not in kwargs["env"]


@pytest.mark.parametrize("provider, model, expected", [
    ("claude_cli", None, ("claude_cli", "opus")),
    ("claude_cli", "sonnet", ("claude_cli", "sonnet")),
    ("codex_cli", "configured-model", ("codex_cli", "configured-model")),
])
def test_section_draft_uses_an_explicitly_configured_drafter(
    workflow, monkeypatch, provider, model, expected
):
    service, codex, _ = workflow
    monkeypatch.setenv("YT_RAG_LABEL_DRAFTER", provider)
    if model:
        monkeypatch.setenv("YT_RAG_LABEL_DRAFTER_MODEL", model)
    codex.output = {"sections": [{"title": "Whole lesson", "start_line": 1, "end_line": 5}]}
    job = service.start_job("sections", [SOURCE_ID], "en")
    assert wait_for_job(service, job["job_id"])["status"] == "completed"
    assert codex.selection == expected


def test_section_draft_keeps_sections_on_lines_left_out_of_a_sampled_transcript(
    workflow, monkeypatch
):
    service, codex, _ = workflow
    # A budget this small keeps only some lines, as with a multi-hour episode.
    monkeypatch.setattr("local_preview.label_drafts.MAX_CONTEXT_CHARS", 110)
    codex.output = {"sections": [
        {"title": "Lane and cart", "start_line": 1, "end_line": 2},
        {"title": "Sorting and gate", "start_line": 3, "end_line": 4},
    ]}
    job = service.start_job("sections", [SOURCE_ID], "en")
    finished = wait_for_job(service, job["job_id"])
    assert finished["status"] == "completed", finished
    prompt, _ = codex.calls[0]
    assert "[0003]" in prompt and "[0002]" not in prompt and "[0004]" not in prompt
    draft = service.get_draft(finished["draft_id"])
    assert [(item["start"], item["end"]) for item in draft["items"]] == [(0.25, 34), (49, 90)]
    assert all("lines" not in source and "sample" not in source for source in draft["sources"])


def test_section_draft_resolves_lines_and_publishes_reviewed_boundary_seconds(workflow, monkeypatch):
    service, codex, source = workflow
    codex.output = {
        "sections": [
            {"title": "Gate and cleanup", "start_line": 4, "end_line": 5},
            {"title": "Cart clearance", "start_line": 1, "end_line": 3},
            {"title": "Bad line", "start_line": 30, "end_line": 40},
        ],
        "notes": "Check the transition.",
    }
    job = service.start_job("sections", [SOURCE_ID], "en")
    finished = wait_for_job(service, job["job_id"])
    assert finished["status"] == "completed"
    assert codex.selection == ("codex_cli", None)
    prompt, schema = codex.calls[0]
    assert "blocks the show itself treats as one unit" in prompt
    assert "There is no target number of sections" in prompt
    assert "Leave ad reads out entirely" in prompt
    assert "[0004] 1:20 Swing the gate outward." in prompt
    assert source["title"] not in prompt and source["summary"] not in prompt
    assert schema.name == "label_sections.schema.json" and schema.is_file()

    draft_id = finished["draft_id"]
    first, second = service.get_draft(draft_id)["items"]
    assert (first["title"], first["start_line"], first["end_line"]) == ("Cart clearance", 1, 3)
    assert (first["start"], first["end"], second["start"], second["end"]) == (
        0.25, 55, 80, 115,
    )
    bad_review = {"id": second["id"], "decision": "edited", "edits": {"start": 120}}
    with pytest.raises(LabelDraftError, match="Section start must precede"):
        service.save_review(draft_id, [bad_review])
    service.save_review(draft_id, [
        {"id": first["id"], "decision": "approved"},
        {"id": second["id"], "decision": "edited", "edits": {"start": 79.25, "end": 113}},
    ])
    assert service.context(draft_id, second["id"])["start"] == 59.25
    manifest = service.finalize(draft_id)
    path = service.labels_dir / "sections" / f"{manifest['dataset_id']}.jsonl"
    first, second = map(json.loads, path.read_text().splitlines())
    assert (first["start"], first["end"], second["start"], second["end"]) == (
        0.25, 55, 79.25, 113,
    )
    assert second["title"] == "Gate and cleanup" and second["task"] == "sections"
    assert "start_line" not in second and "end_line" not in second
