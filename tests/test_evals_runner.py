"""Tests for the offline benchmark runner."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.runner import run_benchmark, run_one_config


ROOT_DIR = Path(__file__).resolve().parents[1]


def test_runner_writes_reports_and_selects_optimized_config(tmp_path):
    result = run_benchmark(
        ROOT_DIR / "evals" / "datasets" / "jp_core_v1.example.jsonl",
        ROOT_DIR / "evals" / "configs" / "baseline.yaml",
        tmp_path / "latest",
    )

    assert (tmp_path / "latest" / "results.json").exists()
    assert (tmp_path / "latest" / "leaderboard.md").exists()
    assert (tmp_path / "latest" / "failures.md").exists()
    assert (tmp_path / "latest" / "per_query_results.jsonl").exists()
    assert result["selected_run"]["name"] == "optimized_hybrid"
    assert result["thresholds"]["small_sample"] is True
    # Quality checks are deterministic on the fixture. The p95 latency check
    # compares sub-millisecond wall-clock timings, so CI noise can fail it; the
    # CLI still enforces it with --fail-on-threshold.
    checks = result["thresholds"]["checks"]
    assert any(check["metric"] == "p95_latency_ms" for check in checks)
    quality_checks = [check for check in checks if check["metric"] != "p95_latency_ms"]
    assert quality_checks
    assert all(check["passed"] for check in quality_checks), quality_checks
    agentic = next(run for run in result["runs"] if run["name"] == "agentic")
    assert agentic["strategy"] == "agentic"
    assert "Precision@5" in agentic["metrics"]
    assert "Precision@10" in agentic["metrics"]
    trace = agentic["per_query"][0]["retrieval_details"]["agentic_retrieval"]
    assert trace["policy"] == "deterministic_tool_policy_v1"
    assert trace["attempts"][-1]["tool"] == "read_context"


@pytest.mark.parametrize("status", ["error", "degraded"])
def test_runner_counts_stage_failures_even_when_retrieval_returns(status):
    service = SimpleNamespace(
        retrieve=lambda *args, **kwargs: {
            "results": [],
            "details": {
                "status": status,
                "stages": {"dense": {"status": "error", "error_type": "RuntimeError"}},
            },
        }
    )
    result = run_one_config(
        service,
        [{"id": "failure", "query": "test"}],
        {"retrieval_mode": "hybrid"},
        default_top_k=5,
    )
    assert result["metrics"]["failed_query_count"] == 1
    assert result["per_query"][0]["retrieval_details"]["status"] == status


@pytest.mark.parametrize(
    "failed_attempt",
    [
        {"status": "error"},
        {"status": "degraded"},
        {"status": "ok", "stages": {"dense": {"status": "error"}}},
    ],
)
def test_agentic_runner_counts_failed_attempt_even_when_final_search_succeeds(
    failed_attempt,
):
    retrieval = {
        "results": [{"video_id": "fixture", "chunk_index": 0}],
        "details": {
            "status": "ok",
            "agentic_retrieval": {
                "attempts": [failed_attempt, {"status": "ok"}],
            },
        },
    }
    service = SimpleNamespace(
        retrieve_agentic=lambda *args, **kwargs: {"retrieval": retrieval}
    )
    result = run_one_config(
        service,
        [{"id": "failed-then-recovered", "query": "test"}],
        {"strategy": "agentic"},
        default_top_k=5,
    )
    assert result["metrics"]["failed_query_count"] == 1
    assert result["per_query"][0]["retrieval_details"] == retrieval["details"]


@pytest.mark.parametrize("status", ["skipped", "no_matches"])
def test_agentic_runner_does_not_count_empty_or_skipped_attempt_as_failure(status):
    service = SimpleNamespace(
        retrieve_agentic=lambda *args, **kwargs: {
            "retrieval": {
                "results": [],
                "details": {
                    "status": "no_matches",
                    "agentic_retrieval": {
                        "attempts": [{"status": status}],
                    },
                },
            }
        }
    )
    result = run_one_config(
        service,
        [{"id": "empty", "query": "test"}],
        {"strategy": "agentic"},
        default_top_k=5,
    )
    assert result["metrics"]["failed_query_count"] == 0
