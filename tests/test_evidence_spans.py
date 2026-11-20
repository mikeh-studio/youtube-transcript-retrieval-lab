"""Offline tests for span matching and migration."""

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.evidence_spans import chunk_rows_to_spans, normalize_gold_evidence, span_matches
from evals.migrate_gold_spans import main as migrate
from evals.runner import run_benchmark, run_one_config
from evals.scoring import QUALITY_METRICS, gold_matches_result


ROOT = Path(__file__).resolve().parents[1]


def test_normalize_gold_evidence_writes_canonical_keys():
    assert normalize_gold_evidence({
        "source_id": "synthetic",
        "start": 0,
        "end": 60,
        "chunk_index": 0,
        "chunking_version": "window-v1",
        "grade": 2,
    }) == {
        "video_id": "synthetic",
        "start": 0,
        "end": 60,
        "legacy_chunk_index": 0,
        "chunking_version": "window-v1",
        "grade": 2,
    }


@pytest.mark.parametrize("bounds", [
    {"start": 0},
    {"start": 2, "end": 1},
    {"start": 0, "end": float("inf")},
    {"start": float("nan"), "end": 1},
])
def test_normalize_rejects_invalid_bounds_even_with_legacy_index(bounds):
    with pytest.raises(ValueError, match="finite start and end"):
        normalize_gold_evidence({"video_id": "synthetic", "chunk_index": 0, **bounds})


def test_span_match_measures_overlap_against_shorter_duration():
    gold = {"start": 0, "end": 60}
    result = {"start": 50, "end": 70}
    assert span_matches(gold, result)
    assert span_matches(result, gold)
    assert not span_matches(gold, result, min_overlap_ratio=0.51)


def test_neighbor_chunk_cannot_match_by_index_when_gold_has_a_span():
    gold = {"video_id": "synthetic", "start": 0, "end": 60, "chunk_index": 0}
    neighbor = {"video_id": "synthetic", "start": 45, "end": 105, "chunk_index": 0}
    assert not gold_matches_result(gold, neighbor)
    assert gold_matches_result(gold, neighbor, min_overlap_ratio=0.25)
    assert not gold_matches_result(gold, {"video_id": "synthetic", "chunk_index": 0})


@pytest.mark.parametrize(("point", "expected"), [(4, False), (5, True), (10, True), (11, False)])
def test_point_gold_matches_inside_or_on_boundary(point, expected):
    assert span_matches({"start": point, "end": point}, {"start": 5, "end": 10}) is expected


def test_matching_span_survives_rechunking():
    gold = {"source_id": "synthetic", "start": 0, "end": 60,
            "legacy_chunk_index": 0, "chunking_version": "window-v1"}
    result = {"video_id": "synthetic", "start": 0, "end": 50,
              "chunk_index": 3, "chunking_version": "window-v2"}
    assert gold_matches_result(gold, result)
    assert not gold_matches_result(gold, {**result, "video_id": "different"})


@pytest.mark.parametrize("key", ["chunk_index", "legacy_chunk_index"])
def test_legacy_fallback_checks_index_and_version(key):
    gold = {"video_id": "synthetic", key: 0, "chunking_version": "window-v1"}
    result = {"video_id": "synthetic", "chunk_index": 0}
    assert gold_matches_result(gold, result)
    assert gold_matches_result(gold, {**result, "chunking_version": "window-v1"})
    assert not gold_matches_result(gold, {**result, "chunk_index": 1})
    with pytest.raises(ValueError, match="Chunking version mismatch.*window-v1.*window-v2"):
        gold_matches_result(gold, {**result, "chunking_version": "window-v2"})


def test_runner_checks_corpus_version_when_result_omits_it():
    video = {"chunking": {"version": "window-v2"}}
    service = SimpleNamespace(
        engine=SimpleNamespace(library=SimpleNamespace(videos={"synthetic": video})),
        retrieve=lambda *args, **kwargs: {
            "results": [{"video_id": "synthetic", "chunk_index": 0}]
        },
    )
    dataset = [{"id": "case", "query": "Where is the spare key?", "gold_evidence": [
        {"video_id": "synthetic", "chunk_index": 0, "chunking_version": "window-v1"}
    ]}]
    run = run_one_config(service, dataset, {"retrieval_mode": "dense"}, default_top_k=1)
    assert run["metrics"]["failed_query_count"] == 1
    assert "Chunking version mismatch" in run["per_query"][0]["error"]


def test_migration_is_safe_to_run_in_place(tmp_path):
    dataset_path = tmp_path / "dataset.jsonl"
    corpus_path = tmp_path / "corpus.json"
    legacy = {"video_id": "synthetic", "chunk_index": 0, "grade": 2}
    span = {"video_id": "synthetic", "start": 10, "end": 20}
    row = {"id": "case", "gold_evidence": [legacy, span]}
    dataset_path.write_text(json.dumps(row) + "\n")
    corpus_path.write_text(json.dumps({"videos": [{
        "video_id": "synthetic", "chunking_version": "window-v1",
        "chunks": [{"start": 3, "end": 64}],
    }]}))
    args = ["--dataset", str(dataset_path), "--corpus", str(corpus_path), "--out", str(dataset_path)]

    assert migrate(args) == 0
    migrated = dataset_path.read_bytes()
    assert json.loads(migrated) == {
        **row,
        "gold_evidence": [{
            "video_id": "synthetic", "start": 3, "end": 64, "legacy_chunk_index": 0,
            "chunking_version": "window-v1", "grade": 2,
        }, span],
    }
    assert stat.S_IMODE(dataset_path.stat().st_mode) == 0o600
    assert migrate(args) == 0
    assert dataset_path.read_bytes() == migrated

    invalid = json.dumps({**row, "gold_evidence": [{**legacy, "chunk_index": 5}]}) + "\n"
    dataset_path.write_text(invalid)
    with pytest.raises(SystemExit) as error:
        migrate(args)
    assert error.value.code == 2
    assert dataset_path.read_text() == invalid


def test_runner_uses_configured_overlap_ratio(tmp_path, monkeypatch):
    dataset_path, config_path = tmp_path / "dataset.jsonl", tmp_path / "config.yaml"
    dataset_path.write_text(json.dumps({
        "id": "case", "language": "en", "query": "Where is the spare key?",
        "query_type": "fact_lookup", "difficulty": "easy", "required_facts": [], "notes": "",
        "gold_evidence": [{"source_id": "synthetic", "start": 0, "end": 60}],
    }) + "\n")
    config_path.write_text(json.dumps({
        "span_min_overlap_ratio": 0.25, "runs": [{"name": "dense", "retrieval_mode": "dense"}],
    }))
    service = SimpleNamespace(retrieve=lambda *args, **kwargs: {
        "results": [{"video_id": "synthetic", "start": 45, "end": 105}]
    })
    monkeypatch.setattr("evals.runner.build_service", lambda *args: service)

    result = run_benchmark(dataset_path, config_path, tmp_path / "report")

    metrics = result["runs"][0]["metrics"]
    assert metrics["failed_query_count"] == 0
    assert {metric: metrics[metric] for metric in QUALITY_METRICS} == {
        "Precision@1": 1.0, "Precision@5": 0.2, "Precision@10": 0.1,
        "gold_recall@1": 1.0, "gold_recall@5": 1.0, "gold_recall@10": 1.0,
        "MRR@10": 1.0, "nDCG@10": 1.0,
    }


def test_migrated_fixture_preserves_every_run_quality_metric(tmp_path):
    baseline_path = ROOT / "tests/fixtures/eval_baselines/jp_core_v1_quality_metrics.json"
    baseline = json.loads(baseline_path.read_text())
    result = run_benchmark(
        ROOT / "evals/datasets/jp_core_v1.example.jsonl",
        ROOT / "evals/configs/baseline.yaml",
        tmp_path / "reports",
    )

    assert {run["name"] for run in result["runs"]} == set(baseline)
    for run in result["runs"]:
        expected = baseline[run["name"]]
        assert set(expected) == set(QUALITY_METRICS)
        assert run["metrics"]["failed_query_count"] == 0
        for metric in QUALITY_METRICS:
            assert abs(run["metrics"][metric] - expected[metric]) < 1e-9, (run["name"], metric)


def test_migration_uses_explicit_chunk_index_not_list_position():
    corpus = {
        "chunking_version": "v_test",
        "videos": [
            {
                "video_id": "vid",
                "chunks": [
                    {"chunk_index": 2, "start": 120, "end": 180},
                    {"chunk_index": 1, "start": 60, "end": 120},
                ],
            }
        ],
    }
    rows = [{"id": "q1", "gold_evidence": [{"video_id": "vid", "chunk_index": 1}]}]

    migrated = chunk_rows_to_spans(rows, corpus)

    gold = migrated[0]["gold_evidence"][0]
    assert (gold["start"], gold["end"]) == (60, 120)
    assert gold["legacy_chunk_index"] == 1
    with pytest.raises(ValueError, match="missing chunk 0"):
        chunk_rows_to_spans(
            [{"id": "q2", "gold_evidence": [{"video_id": "vid", "chunk_index": 0}]}],
            corpus,
        )
