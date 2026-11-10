"""Focused checks for the ungraded offline performance contract."""

import json

import pytest

from evals.performance_runner import (
    CONFIG,
    _prepare_output,
    copy_records,
    generated_workload,
    measure_requests,
    nearest_rank,
    record_hashes,
    summarize_samples,
    validate_model_snapshot_path,
    validate_workload,
)


def _videos():
    return {
        f"{lang}-{number}": {
            "language": lang,
            "chunks": [
                {"raw_text": f"{lang} sample transcript content number {number}"}
            ],
        }
        for lang in ("en", "ja")
        for number in range(3)
    }


def _record(video_id):
    return {
        "language": "en",
        "title": "private title",
        "url": "private URL",
        "chunks": [
            {
                "raw_text": "private transcript",
                "start": 0,
                "end": 2,
                "embed_text": "private transcript",
            }
        ],
    }


def test_workload_is_ungraded_and_rejects_quality_fields():
    videos = _videos()
    workload = generated_workload(videos)
    validate_workload(workload, videos)
    assert len(workload["queries"]) == 6
    assert {q["scope"]["kind"] for q in workload["queries"]} == {"global", "video"}
    workload["queries"][0]["judgments"] = {"pretend": 1}
    with pytest.raises(ValueError, match="query fields"):
        validate_workload(workload, videos)
    del workload["queries"][0]["judgments"]
    workload["queries"][0]["scope"] = {"kind": "video", "video_id": "missing"}
    with pytest.raises(ValueError, match="scope"):
        validate_workload(workload, videos)


def test_copy_records_excludes_index_and_detects_changes(tmp_path):
    source = tmp_path / "source"
    record = source / "library" / "videos" / "id1" / "record.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps(_record("id1")))
    manifest = source / "library" / "library.json"
    manifest.write_text(json.dumps({"videos": ["id1"]}))
    index = source / "index" / "library.faiss"
    index.parent.mkdir()
    index.write_bytes(b"untrusted")
    target = tmp_path / "target"
    expected = record_hashes(source)
    assert copy_records(source, target) == expected
    assert not (target / "index").exists()
    manifest.write_text(json.dumps({"videos": ["missing"]}))
    with pytest.raises(ValueError, match="record set"):
        record_hashes(source)
    manifest.write_text(json.dumps({"videos": ["id1"]}))
    record.write_text(json.dumps({"changed": True}))
    assert record_hashes(source) != expected


def test_output_must_be_new_and_outside_inputs(tmp_path):
    forbidden = tmp_path / "repo"
    forbidden.mkdir()
    with pytest.raises(ValueError, match="outside"):
        _prepare_output(forbidden / "report", [forbidden])
    destination = tmp_path / "private"
    _prepare_output(destination, [forbidden])
    with pytest.raises(FileExistsError):
        _prepare_output(destination, [forbidden])


def test_cached_model_path_requires_e5_large_owner_and_revision(tmp_path):
    revision = "a" * 40
    good = tmp_path / "models--intfloat--multilingual-e5-large" / "snapshots" / revision
    wrong = tmp_path / "models--other--model" / "snapshots" / revision
    for path in (good, wrong):
        path.mkdir(parents=True)
        (path / "modules.json").write_text("[]")
    assert validate_model_snapshot_path(good, revision) == revision
    with pytest.raises(ValueError, match="intfloat/multilingual-e5-large"):
        validate_model_snapshot_path(wrong, revision)
    with pytest.raises(ValueError, match="revision does not match"):
        validate_model_snapshot_path(good, "b" * 40)


class _Service:
    def __init__(self, *, failed=False, changing=False):
        self.failed = failed
        self.changing = changing
        self.calls = 0

    def retrieve(self, _text, **_kwargs):
        self.calls += 1
        row = {
            "video_id": "id1",
            "chunk_index": 0,
            "start": 1,
            "end": 2,
            "score": float(self.calls if self.changing else 1),
            "text": "evidence",
        }
        return {
            "details": {
                "status": "degraded" if self.failed else "ok",
                "stages": {
                    "dense": {
                        "status": "error" if self.failed else "ok",
                        "error_type": "RuntimeError" if self.failed else None,
                    },
                },
                "fallback": "lexical_only" if self.failed else None,
            },
            "results": [row],
        }


def _one_query():
    return {
        "queries": [
            {"id": "q1", "text": "probe", "language": "en", "scope": {"kind": "global"}}
        ]
    }


def test_degraded_results_are_failed_even_when_nonempty():
    config = {**CONFIG, "modes": ["hybrid"], "warm_repetitions": 2}
    samples, parity = measure_requests(_Service(failed=True), _one_query(), config)
    assert len(samples) == 3
    assert all(sample["status"] == "failed_or_degraded" for sample in samples)
    assert all(sample["result_count"] == 1 for sample in samples)
    assert parity == {"q1:hybrid": False}
    assert summarize_samples(samples)["warm:hybrid"]["successful"] == 0


def test_repeated_rank_signature_includes_scores():
    config = {**CONFIG, "modes": ["dense"], "warm_repetitions": 2}
    stable, stable_parity = measure_requests(_Service(), _one_query(), config)
    changed, changed_parity = measure_requests(
        _Service(changing=True), _one_query(), config
    )
    assert stable_parity == {"q1:dense": True}
    assert changed_parity == {"q1:dense": False}
    assert stable[0]["ranks"][0]["evidence_sha256"]
    assert stable[0]["phase"] == "first_use"
    assert [sample["phase"] for sample in stable[1:]] == ["warm", "warm"]


def test_nearest_rank_is_explicit_for_small_samples():
    assert nearest_rank([], 0.95) is None
    assert nearest_rank([4, 1, 3, 2], 0.50) == 2
    assert nearest_rank([4, 1, 3, 2], 0.95) == 4
