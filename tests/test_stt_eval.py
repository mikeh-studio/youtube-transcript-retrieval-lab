import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from evals.stt.drift import align_drift
from evals.stt.metrics import cer, normalize_text, wer
from evals.stt.runner import load_labels, main, score_references
from local_preview.label_drafts import LabelDraftService
from tests.test_label_drafts import wait_for_job
from tests.test_label_drafts_api import local_api, request


def test_stt_normalization_and_errors():
    assert normalize_text("  ＨＥＬＬＯ，  it’s warm！  ", "en") == "hello its warm"
    assert normalize_text("　Ａ 蝶、です。 ", "ja-JP") == "a蝶です"
    assert wer("one small boat", "one bright boat now") == pytest.approx(2 / 3)
    assert wer("one small boat", "one boat") == pytest.approx(1 / 3)
    assert wer("", "boat now") == 2
    assert cer("小舟", "小石") == 0.5
    assert cer("", "") == 0
    assert cer("ab", "abc") == 0.5


def timed(words, shift=0):
    return [{"word": word, "start": index + shift} for index, word in enumerate(words.split())]


def test_drift_uses_unique_normalized_trigram_centers():
    caption = timed("One blue kite rises gently. Go go go go")
    stt = timed("one blue kite rises gently go go go go", 0.5)
    stt[4]["start"] += 0.5
    assert align_drift(caption, stt) == {
        "offsets_seconds": [0.5, 0.5, 0.5, 1.0, 0.5],
        "anchor_count": 5, "median_seconds": 0.5, "iqr_seconds": 0.0,
    }
    assert align_drift(timed("one two three four"), timed("one two three four", -0.5)[1:]) == {
        "offsets_seconds": [-0.5], "anchor_count": 1, "median_seconds": -0.5, "iqr_seconds": 0.0,
    }


def test_drift_iqr_and_crossing_anchors():
    caption = timed("one two three four five six")
    stt = timed("one two three four five six")
    for index, seconds in enumerate((0, 0, 1, 2, 3, 4)):
        stt[index]["start"] += seconds
    result = align_drift(caption, stt)
    assert result["offsets_seconds"] == [0, 1, 2, 3]
    assert (result["median_seconds"], result["iqr_seconds"]) == (1.5, 1.5)
    assert align_drift(timed("red paper kite green wooden boat"), timed("green wooden boat red paper kite"))["anchor_count"] == 1
    assert align_drift(timed("red blue green"), timed("oak ash pine")) == {
        "offsets_seconds": [], "anchor_count": 0, "median_seconds": None, "iqr_seconds": None,
    }
    with pytest.raises(ValueError, match="nondecreasing"):
        align_drift(list(reversed(caption)), stt)


def test_japanese_reference_uses_cer_without_word_segmentation():
    group, = score_references([{
        "source_id": "aud_fixture", "start": 1.0, "end": 2.0, "language": "ja",
        "review_decision": "edited", "text": "小舟。", "draft_text": "小 石",
        "stt": {"engine": "fake", "model": "test"},
    }])
    assert group["cer"] == 0.5
    assert group["wer"] is None


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    record = {
        "title": "Paint swatch notes",
        "language": "en",
        "source": {
            "platform": "podcast_rss", "local_media_path": "media/pod_fixture.mp3",
            "media_url": "https://radio.example/swatch.mp3",
            "episode": {"duration_seconds": 100},
            "stt": {"engine": "fake", "model": "test", "audio_sha256": "1" * 64},
        },
        "full_transcript": {"segments": [
            {"start": index * 4 + 2, "end": index * 4 + 4, "text": "Paint the bench."}
            for index in range(20)
        ]},
    }
    drafter = Mock(side_effect=AssertionError("Unexpected model call"))
    service = LabelDraftService(
        Path(__file__).resolve().parents[1], tmp_path / "runtime", tmp_path / "evaluation",
        {"pod_fixture": record}.get, drafter, lambda: "2026-01-01T00:00:00Z",
    )
    monkeypatch.setattr(local_api, "SERVICE", SimpleNamespace(_label_draft_service=lambda: service))
    yield service, record
    drafter.assert_not_called()


def start_draft(service, task, seed=11):
    status, payload = request("POST", "/v1/labels/jobs", {
        "task": task, "source_ids": ["pod_fixture"], "language": "en", "seed": seed,
    })
    assert status == 202
    job = wait_for_job(service, payload["job"]["job_id"])
    assert job["status"] == "completed", job
    return service.get_draft(job["draft_id"])


def _finalize(service, draft, edits, approve_second=False):
    first, second, *_ = draft["items"]
    decisions = [
        {"id": item["id"], "decision": "rejected"} for item in draft["items"]
    ]
    decisions[0].update(decision="edited", edits=edits)
    if approve_second:
        decisions[1].update(id=second["id"], decision="approved")
    path = f"/v1/labels/drafts/{draft['draft_id']}"
    assert request("POST", f"{path}/review", {"decisions": decisions})[0] == 200
    status, payload = request("POST", f"{path}/finalize", {})
    assert status == 200
    data, manifest = service._dataset_paths(draft)
    assert data.stat().st_mode & 0o777 == manifest.stat().st_mode & 0o777 == 0o600
    return manifest, payload["dataset"]


def test_audio_drafts_and_offline_runner(workflow, tmp_path):
    service, record = workflow
    record["source"]["episode"]["duration_seconds"] = 35
    landing = start_draft(service, "ts_landing")
    repeated = start_draft(service, "ts_landing")
    different = start_draft(service, "ts_landing", seed=12)
    assert landing["label_provenance"]["seed"] == 11
    assert landing["sources"][0]["duration"] == 80
    assert len(landing["items"]) == 10
    assert landing["items"] == repeated["items"] != different["items"]
    first = landing["items"][0]
    path = f"/v1/labels/drafts/{landing['draft_id']}"
    assert request("GET", f"{path}/context?item={first['id']}")[1]["context"]["lines"]
    assert request("POST", f"{path}/review", {"decisions": [
        {"id": first["id"], "decision": "edited", "edits": {"offset_seconds": -100}},
    ]})[0] == 400

    landing_manifest, manifest = _finalize(service, landing, {"offset_seconds": -1.5}, approve_second=True)
    assert manifest["review_counts"] == {"approved": 1, "edited": 1, "rejected": 8}
    reference = start_draft(service, "stt_reference")
    ref_manifest, _ = _finalize(service, reference, {"text": "Paint the fence."})
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": [
        {"job_id": "job_measured", "video_id": "pod_fixture", "status": "completed",
         "source_type": "podcast_rss", "transcription_seconds": 20, "audio_duration_seconds": 100},
        {"job_id": "job_reused", "video_id": "pod_fixture", "status": "completed", "source_type": "podcast_rss"},
    ]}))
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps({"pairs": [{
        "pair_id": "pair_one", "language": "en", "caption_words": timed("one blue kite"),
        "stt_words": timed("one blue kite", 0.25),
    }]}))
    output = tmp_path / "results.json"
    main(["--references", str(ref_manifest), "--landing", str(landing_manifest),
          "--jobs", str(jobs), "--drift-pairs", str(pairs), "--out", str(output)])
    report = json.loads(output.read_text())
    assert report["schema"] == "stt-evaluation/v1"
    assert report["references"][0]["wer"] == pytest.approx(1 / 3)
    assert report["landing"]["landing_rate"] == 0.5
    assert report["landing"]["measured_offsets_seconds"] == [-1.5]
    assert report["drift"][0]["median_seconds"] == 0.25
    assert report["rtf"]["median_rtf"] == 0.2
    assert report["rtf"]["unavailable_count"] == 1
    assert "Paint" not in output.read_text()
    assert output.stat().st_mode & 0o777 == 0o600
    service._dataset_paths(reference)[0].write_text("{}\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_labels([ref_manifest], "stt_reference")


def test_changed_stt_provenance_blocks_finalization(workflow):
    service, record = workflow
    draft = start_draft(service, "stt_reference")
    path = f"/v1/labels/drafts/{draft['draft_id']}"
    assert request("POST", f"{path}/review", {"decisions": [
        {"id": item["id"], "decision": "approved"} for item in draft["items"]
    ]})[0] == 200
    record["source"]["stt"]["audio_sha256"] = "2" * 64
    assert request("POST", f"{path}/finalize", {})[0] == 409
