"""Compare completed real dev runs; this tool never changes an active system.

The 95% intervals are query-level percentile bootstraps. Decisions are local
experiment checks, not evidence that a candidate should be deployed.
"""

import argparse
import json
import math
from pathlib import Path

from evals.contracts import require_private
from evals.statistics import bootstrap_paired_delta_ci

METRICS = ("recall@5", "recall@10", "mrr@10", "ndcg@10")


def _number(value, name, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"invalid {name}")
    return number


def _nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _report(report, run_name=None):
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("invalid report schema")
    if (
        report.get("kind") != "real_retrieval"
        or report.get("status") != "completed"
        or report.get("split") != "dev"
    ):
        raise ValueError("only completed real dev reports are eligible")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("missing provenance")
    for key in ("dataset_sha256", "corpus_sha256", "scoring_version"):
        _nonempty(provenance.get(key), key)
    if provenance.get("evidence_level") != "user_asserted_reviewed":
        raise ValueError("tuning requires user-asserted reviewed real evidence")
    embedding = provenance.get("embedding")
    if (
        not isinstance(embedding, dict)
        or embedding.get("backend") != "sentence_transformers"
    ):
        raise ValueError("a real sentence_transformers embedding backend is required")
    for key in ("model", "revision"):
        _nonempty(embedding.get(key), "embedding " + key)
    policy = provenance.get("timing_policy")
    if not isinstance(policy, dict) or not policy:
        raise ValueError("missing timing policy")
    if (
        policy.get("warmup") != "one_untimed_checked_call_per_query"
        or policy.get("warmup_excluded") is not True
        or policy.get("setup_excluded") is not True
        or policy.get("scope") != "application_retrieval"
        or policy.get("p95") != "nearest_rank"
    ):
        raise ValueError("unsupported timing policy: latency comparison requires checked per-query warmup")
    runs = report.get("runs")
    if (
        not isinstance(runs, list)
        or not runs
        or any(not isinstance(run, dict) for run in runs)
    ):
        raise ValueError("each report must have at least one run")
    names = [_nonempty(run.get("name"), "run name") for run in runs]
    if len(set(names)) != len(names):
        raise ValueError("run names must be unique")
    if run_name is None:
        if len(runs) != 1:
            raise ValueError("select a run for a multi-run report")
        run = runs[0]
    else:
        if run_name not in names:
            raise ValueError("selected run not found")
        run = runs[names.index(run_name)]
    if type(run.get("failed_query_count")) is not int or run["failed_query_count"] != 0:
        raise ValueError("failed or missing query count")
    queries = run.get("per_query")
    if (
        not isinstance(queries, list)
        or not queries
        or type(run.get("query_count")) is not int
        or run["query_count"] != len(queries)
    ):
        raise ValueError("query count must match nonempty per_query")
    rows = {}
    times = []
    for query in queries:
        if not isinstance(query, dict):
            raise ValueError("invalid query")
        query_id = _nonempty(query.get("id"), "query id")
        if query_id in rows or query.get("error", "missing") is not None:
            raise ValueError("duplicate or failed query")
        if type(query.get("unanswerable")) is not bool:
            raise ValueError("missing unanswerable status")
        ranking = query.get("ranked_ids")
        if not isinstance(ranking, list) or any(
            not isinstance(x, str) or not x.strip() for x in ranking
        ):
            raise ValueError("invalid ranking")
        if len(ranking) != len(set(ranking)):
            raise ValueError("ranking IDs must be unique")
        metrics = query.get("metrics")
        if query["unanswerable"]:
            if metrics is not None and (
                not isinstance(metrics, dict)
                or any(metrics.get(k) is not None for k in METRICS)
            ):
                raise ValueError("unanswerable ranking metrics must be null")
        else:
            if not isinstance(metrics, dict):
                raise ValueError("missing query metrics")
            for metric in METRICS:
                value = _number(metrics.get(metric), metric, minimum=0)
                if value > 1:
                    raise ValueError("ranking metric outside [0, 1]")
        query_times = query.get("latency_ms")
        if not isinstance(query_times, list) or not query_times:
            raise ValueError("missing query latency samples")
        times.extend(
            _number(value, "query latency", minimum=0) for value in query_times
        )
        rows[query_id] = query
    latency = run.get("latency_ms")
    if not isinstance(latency, dict):
        raise ValueError("missing latency")
    p95 = _number(latency.get("p95"), "p95 latency", minimum=0)
    if (
        latency.get("p95_method") != "nearest_rank"
        or type(latency.get("samples")) is not int
        or latency["samples"] != len(times)
    ):
        raise ValueError("invalid latency sample count or method")
    if p95 != sorted(times)[math.ceil(0.95 * len(times)) - 1]:
        raise ValueError("p95 does not match query latency samples")
    return provenance, rows, p95, run


def compare_reports(
    baseline,
    candidate,
    *,
    hypothesis,
    variable,
    kind,
    latency_budget_ms,
    metric="ndcg@10",
    minimum_gain=None,
    noninferiority_margin=None,
    seed=0,
    samples=10000,
    baseline_run=None,
    candidate_run=None,
):
    """Compare aligned reports with predeclared thresholds; raise on invalid data."""
    _nonempty(hypothesis, "hypothesis")
    _nonempty(variable, "variable")
    if kind not in ("quality", "performance", "refactor") or metric not in METRICS:
        raise ValueError("invalid kind or metric")
    budget = _number(latency_budget_ms, "latency budget", minimum=0)
    if kind == "quality":
        minimum_gain = _number(minimum_gain, "minimum gain", minimum=0)
    if kind == "performance":
        noninferiority_margin = _number(
            noninferiority_margin, "noninferiority margin", minimum=0
        )
    bp, b, bl, br = _report(baseline, baseline_run)
    cp, c, cl, cr = _report(candidate, candidate_run)
    for key in ("dataset_sha256", "corpus_sha256", "scoring_version", "timing_policy"):
        if bp[key] != cp[key]:
            raise ValueError(f"mismatched {key}")
    if b.keys() != c.keys():
        raise ValueError("query IDs do not match")
    for key in b:
        if b[key]["unanswerable"] != c[key]["unanswerable"]:
            raise ValueError("query answerability does not match")
    answerable = [key for key in sorted(b) if not b[key]["unanswerable"]]
    if not answerable:
        raise ValueError("no answerable queries")
    ci = bootstrap_paired_delta_ci(
        [(key, b[key]["metrics"][metric]) for key in answerable],
        [(key, c[key]["metrics"][metric]) for key in answerable],
        seed=seed,
        samples=samples,
    )
    within_budget = cl <= budget
    if kind == "quality":
        passed = ci["lower"] > 0 and ci["lower"] >= minimum_gain and within_budget
    elif kind == "performance":
        passed = cl < bl and ci["lower"] >= -noninferiority_margin and within_budget
    else:
        passed = (
            all(b[key]["ranked_ids"] == c[key]["ranked_ids"] for key in b)
            and within_budget
        )
    return {
        "schema_version": 1,
        "kind": kind,
        "hypothesis": hypothesis,
        "variable": variable,
        "split": "dev",
        "metric": metric,
        "paired_delta_ci_95": ci,
        "bootstrap_seed": seed,
        "bootstrap_samples": samples,
        "baseline_provenance": bp,
        "candidate_provenance": cp,
        "baseline_run": {"name": br["name"], "config": br.get("config")},
        "candidate_run": {"name": cr["name"], "config": cr.get("config")},
        "baseline_p95_ms": bl,
        "candidate_p95_ms": cl,
        "latency_budget_ms": budget,
        "minimum_gain": minimum_gain,
        "noninferiority_margin": noninferiority_margin,
        "ranking_parity": all(
            b[key]["ranked_ids"] == c[key]["ranked_ids"] for key in b
        ),
        "passed": passed,
        "decision": "criteria_met" if passed else "criteria_not_met",
        "interpretation": "Exploratory dev comparison; no adoption or deployment decision.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("compare")
    for flag in ("baseline", "candidate", "hypothesis", "variable", "output"):
        p.add_argument("--" + flag, required=True)
    p.add_argument(
        "--kind", choices=("quality", "performance", "refactor"), required=True
    )
    p.add_argument("--latency-budget-ms", type=float, required=True)
    p.add_argument("--minimum-gain", type=float)
    p.add_argument("--noninferiority-margin", type=float)
    p.add_argument("--metric", choices=METRICS, default="ndcg@10")
    p.add_argument("--baseline-run")
    p.add_argument("--candidate-run")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--samples", type=int, default=10000)
    p.add_argument(
        "--log", help="Append JSONL to this path (default: output path + .jsonl)"
    )
    args = parser.parse_args(argv)
    try:
        for path in (
            args.baseline,
            args.candidate,
            args.output,
            args.log or (args.output + ".jsonl"),
        ):
            require_private(Path(path))
        with open(args.baseline, encoding="utf-8") as f:
            baseline = json.load(f)
        with open(args.candidate, encoding="utf-8") as f:
            candidate = json.load(f)
        result = compare_reports(
            baseline,
            candidate,
            hypothesis=args.hypothesis,
            variable=args.variable,
            kind=args.kind,
            latency_budget_ms=args.latency_budget_ms,
            metric=args.metric,
            minimum_gain=args.minimum_gain,
            noninferiority_margin=args.noninferiority_margin,
            seed=args.seed,
            samples=args.samples,
            baseline_run=args.baseline_run,
            candidate_run=args.candidate_run,
        )
        output = Path(args.output)
        log = Path(args.log or (args.output + ".jsonl"))
        if output.resolve() in {
            Path(args.baseline).resolve(),
            Path(args.candidate).resolve(),
            log.resolve(),
        } or log.resolve() in {
            Path(args.baseline).resolve(),
            Path(args.candidate).resolve(),
        }:
            raise ValueError(
                "output and log must be separate from inputs and each other"
            )
        with output.open("x", encoding="utf-8") as f:
            json.dump(result, f, indent=2, allow_nan=False)
            f.write("\n")
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, allow_nan=False) + "\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
