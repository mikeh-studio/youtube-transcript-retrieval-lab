import copy
import json

import pytest

from evals.experiments import compare_reports, main


def report(score=0.5, p95=20, ranking=None):
    metrics = {k: score for k in ("recall@5", "recall@10", "mrr@10", "ndcg@10")}
    return {
        "schema_version": 1,
        "kind": "real_retrieval",
        "status": "completed",
        "split": "dev",
        "provenance": {
            "dataset_sha256": "dataset",
            "corpus_sha256": "corpus",
            "scoring_version": "graded_v1",
            "evidence_level": "user_asserted_reviewed",
            "timing_policy": {"clock": "perf_counter", "p95": "nearest_rank"},
            "embedding": {
                "backend": "sentence_transformers",
                "model": "e5",
                "revision": "commit",
            },
        },
        "runs": [
            {
                "name": "main",
                "config": {},
                "query_count": 2,
                "failed_query_count": 0,
                "latency_ms": {
                    "p50": p95,
                    "p95": p95,
                    "samples": 2,
                    "p95_method": "nearest_rank",
                },
                "per_query": [
                    {
                        "id": key,
                        "unanswerable": False,
                        "error": None,
                        "metrics": metrics.copy(),
                        "ranked_ids": ranking or ["a"],
                        "latency_ms": [p95],
                    }
                    for key in ("one", "two")
                ],
            }
        ],
    }


def compare(a, b, kind="quality", **kwargs):
    return compare_reports(
        a,
        b,
        hypothesis="improve",
        variable="reranker",
        kind=kind,
        latency_budget_ms=30,
        samples=100,
        **kwargs,
    )


def test_quality_uses_paired_lower_bound_and_budget():
    result = compare(report(), report(0.7), minimum_gain=0.1)
    assert result["passed"]
    assert result["paired_delta_ci_95"]["lower"] == pytest.approx(0.2)
    assert not compare(report(), report(0.7, 31), minimum_gain=0.1)["passed"]
    assert not compare(report(), report(), minimum_gain=0)["passed"]


def test_performance_requires_quality_noninferiority_and_latency():
    assert compare(
        report(), report(0.49, 10), "performance", noninferiority_margin=0.02
    )["passed"]
    assert not compare(
        report(), report(0.4, 10), "performance", noninferiority_margin=0.02
    )["passed"]
    assert not compare(
        report(), report(0.5, 21), "performance", noninferiority_margin=0.02
    )["passed"]
    with pytest.raises(ValueError):
        compare(report(), report(), "performance")


def test_refactor_requires_fixed_rankings():
    assert compare(report(), report(), "refactor")["passed"]
    assert not compare(report(), report(ranking=["b"]), "refactor")["passed"]


def test_ineligible_and_misaligned_rejected():
    for mutate in (
        lambda r: r.update(split="holdout"),
        lambda r: r.update(kind="synthetic"),
        lambda r: r.update(status="failed"),
        lambda r: r["provenance"].update(scoring_version="other"),
        lambda r: r["provenance"].update(evidence_level="real_component_smoke"),
        lambda r: r["provenance"].update(timing_policy={"clock": "other"}),
        lambda r: r["provenance"]["embedding"].update(backend="synthetic"),
        lambda r: r["provenance"].update(corpus_sha256="other"),
        lambda r: r["runs"][0].update(failed_query_count=1),
        lambda r: r["runs"][0]["per_query"][0].update(id="other"),
        lambda r: r["runs"][0]["per_query"][0]["metrics"].update({"recall@5": None}),
        lambda r: r["runs"][0]["per_query"][0]["metrics"].update(
            {"recall@5": float("nan")}
        ),
    ):
        candidate = copy.deepcopy(report())
        mutate(candidate)
        with pytest.raises(ValueError):
            compare(report(), candidate, minimum_gain=0)


def test_unanswerable_is_not_averaged():
    left, right = report(), report(0.7)
    for value in (left, right):
        row = value["runs"][0]["per_query"][0]
        row.update(unanswerable=True, metrics=None)
    assert compare(left, right, minimum_gain=0)["paired_delta_ci_95"]["n"] == 1


def test_cli_never_overwrites_and_appends_log(tmp_path):
    a, b, out = (tmp_path / name for name in ("a.json", "b.json", "result.json"))
    a.write_text(json.dumps(report()))
    b.write_text(json.dumps(report(0.7)))
    args = [
        "compare",
        "--baseline",
        str(a),
        "--candidate",
        str(b),
        "--output",
        str(out),
        "--hypothesis",
        "improve",
        "--variable",
        "reranker",
        "--kind",
        "quality",
        "--latency-budget-ms",
        "30",
        "--minimum-gain",
        "0.1",
        "--samples",
        "100",
    ]
    assert main(args) == 0
    assert json.loads(out.read_text())["passed"]
    log = tmp_path / "result.json.jsonl"
    assert len(log.read_text().splitlines()) == 1
    with pytest.raises(SystemExit):
        main(args)
    assert len(log.read_text().splitlines()) == 1


def test_multi_run_selection_and_duplicate_rank_rejection():
    baseline = report()
    second = copy.deepcopy(baseline["runs"][0])
    second["name"] = "alternate"
    baseline["runs"].append(second)
    with pytest.raises(ValueError, match="select a run"):
        compare(baseline, report(), minimum_gain=0)
    result = compare(baseline, report(), minimum_gain=0, baseline_run="alternate")
    assert result["baseline_run"]["name"] == "alternate"
    with pytest.raises(ValueError, match="not found"):
        compare(baseline, report(), minimum_gain=0, baseline_run="missing")
    broken = report(ranking=["a", "a"])
    with pytest.raises(ValueError, match="unique"):
        compare(report(), broken, minimum_gain=0)


def test_comparison_accepts_runner_generated_shape():
    from evals.real_runner import run_queries

    class StubService:
        def retrieve(self, *args, **kwargs):
            return {"results": [{"video_id": "v", "chunk_index": 0}], "details": {}}

    dataset = [
        {
            "id": "q",
            "query": "example",
            "group": "g",
            "language": "en",
            "query_type": "fact",
            "difficulty": "easy",
            "unanswerable": False,
            "judgments": {"v:0": 2},
        }
    ]
    config = {"top_k": 10, "latency_repetitions": 1}
    run = {"name": "dense", "retrieval_mode": "dense", "reranker": "none"}
    generated = run_queries(StubService(), dataset, run, config)
    left = report()
    left["runs"] = [generated]
    # This checks report compatibility with a stub only; it is not benchmark evidence.
    assert compare(left, copy.deepcopy(left), "refactor")["passed"]
