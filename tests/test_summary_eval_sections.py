"""Verify section metrics and the key areas evaluator entry point offline."""

import json
from types import SimpleNamespace

import pytest

from evals.contracts import ContractError, ROOT
from evals.summary import runner
from evals.summary.dataset import load_gold
from evals.summary.predictions import load_predictions, validate_predictions
from evals.summary.scoring import score
from tests.test_summary_eval import GOLD, MANIFEST, TRANSCRIPTS


def test_section_metrics_against_hand_computed_fixture():
    fixture = json.loads(
        (ROOT / "evals/fixtures/summary_sections_synthetic_v1.json").read_text()
    )
    gold = {
        "key_points": [],
        "sections": fixture["gold"],
        "duration_s": fixture["duration_s"],
        "language": "en",
    }
    prediction = {"items": [], "sections": fixture["predicted"]}
    result = score({"demo": gold}, {("key_areas", "demo"): prediction})["systems"][
        "key_areas"
    ]
    source, aggregate = result["sources"]["demo"], result["aggregate"]
    # Closest pairs at 15s: (40,49). At 30s: (40,49) and (80,55).
    # With k=20, 29 windows differ around the first gold boundary, 20
    # around the second, and 10 around the late predicted boundary.
    # Span union is [0,34], [49,53] and [55,118].
    expected = {
        "boundary_f1@15s": 2 / 5,
        "boundary_f1@30s": 4 / 5,
        "window_diff": 59 / 100,
        "section_coverage": 101 / 120,
    }
    for entry in (source, aggregate):
        assert {metric: entry["metrics"][metric] for metric in expected} == expected
        assert entry["counts"]["window_diff_windows"] == 100


def test_section_metrics_handle_empty_predictions_and_missing_reference():
    gold = {
        "key_points": [],
        "sections": [{"start": 0}],
        "duration_s": 120,
        "language": "en",
    }
    single = {"items": [], "sections": [{"start": 15, "end": 115}]}
    rows = {
        ("single", "demo"): single,
        ("empty", "demo"): {"items": [], "sections": []},
    }
    systems = score({"demo": gold}, rows)["systems"]
    assert systems["single"]["aggregate"]["metrics"]["boundary_f1@15s"] == 1
    assert systems["single"]["aggregate"]["metrics"]["window_diff"] == 0
    assert systems["empty"]["aggregate"]["metrics"]["section_coverage"] == 0

    no_ref = score({"demo": {**gold, "sections": []}}, {("single", "demo"): single})
    metrics = no_ref["systems"]["single"]["aggregate"]["metrics"]
    assert metrics["section_coverage"] == 100 / 120
    assert "boundary_f1@15s" not in metrics


def test_generate_cli_flattens_citations_and_preserves_sections(tmp_path, monkeypatch):
    gold, _ = load_gold(GOLD, MANIFEST)
    seen = []

    def generate_key_areas(source_id, language, provider, model):
        seen.append(source_id)
        point = {
            "text": "An invented note.",
            "speakers": [],
            "evidence_lines": [2, 3],
            "evidence": [{"start": 20, "end": 30}, {"start": 30, "end": 40}],
            "start": 20,
            "end": 40,
        }
        return {
            "provider": provider,
            "model": model,
            "language": language,
            "source_fingerprint": gold[source_id]["source_fingerprint"],
            "generation_details": {"cache_hit": False},
            "sections": [
                {"title": "Warmup", "start": 0, "end": 40, "key_points": [point]},
                {"title": "Finish", "start": 40, "end": 100, "key_points": [point]},
            ],
        }

    monkeypatch.setattr(
        runner,
        "_service",
        lambda sources: SimpleNamespace(
            generate_key_areas=generate_key_areas,
        ),
    )
    output = tmp_path / "predictions.jsonl"
    runner.main(
        [
            "generate",
            "--system",
            "key_areas",
            "--provider",
            "codex_cli",
            "--model",
            "fixture-model",
            "--gold",
            str(GOLD),
            "--manifest",
            str(MANIFEST),
            "--transcripts",
            str(TRANSCRIPTS),
            "--out",
            str(output),
        ]
    )
    predictions = load_predictions(output, gold)
    row = predictions["key_areas", "synthetic_en"]
    assert set(seen) == set(gold)
    assert [item["id"] for item in row["items"]] == ["item_1", "item_2"]
    assert row["items"][0]["evidence_lines"] == [2, 3]
    assert row["items"][0]["timestamp_source"] == "line_ids"
    assert (
        row["items"][0]["evidence"] == row["sections"][0]["key_points"][0]["evidence"]
    )
    assert [section["title"] for section in row["sections"]] == ["Warmup", "Finish"]
    scored = score(gold, predictions)["systems"]["key_areas"]
    assert scored["aggregate"]["metrics"]["anchor_resolution_rate"] == 1

    row["sections"][0]["end"] = -1
    with pytest.raises(ContractError, match="prediction section.end"):
        validate_predictions(list(predictions.values()), gold)
