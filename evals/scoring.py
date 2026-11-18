"""Scoring helpers for offline retrieval benchmarks.

The benchmark scores timestamp spans and supports legacy chunk-index labels.
"""

from __future__ import annotations

import math
from functools import partial
from typing import Dict, Iterable, List, Optional, Sequence

from evals.evidence_spans import chunking_version, normalize_gold_evidence, span_matches


METRIC_PRECISION_1 = "Precision@1"
METRIC_PRECISION_5 = "Precision@5"
METRIC_PRECISION_10 = "Precision@10"
METRIC_RECALL_1 = "gold_recall@1"
METRIC_RECALL_5 = "gold_recall@5"
METRIC_RECALL_10 = "gold_recall@10"
METRIC_MRR_10 = "MRR@10"
METRIC_NDCG_10 = "nDCG@10"

QUALITY_METRICS = (
    METRIC_PRECISION_1,
    METRIC_PRECISION_5,
    METRIC_PRECISION_10,
    METRIC_RECALL_1,
    METRIC_RECALL_5,
    METRIC_RECALL_10,
    METRIC_MRR_10,
    METRIC_NDCG_10,
)


def gold_matches_result(
    gold: dict, result: dict, *, min_overlap_ratio: float = 0.5
) -> bool:
    """Return True when a result satisfies one gold evidence entry."""
    gold = normalize_gold_evidence(gold)
    result_video_id = str(result.get("video_id") or result.get("source_id") or "")
    if gold["video_id"] != result_video_id:
        return False

    if "start" in gold:
        return span_matches(gold, result, min_overlap_ratio=min_overlap_ratio)

    gold_version, result_version = gold.get("chunking_version"), chunking_version(result)
    if gold_version and result_version and gold_version != result_version:
        raise ValueError(
            f"Chunking version mismatch for {result_video_id}: "
            f"gold {gold_version!r}, result {result_version!r}"
        )
    try:
        result_index = int(result.get("chunk_index"))
    except (TypeError, ValueError, OverflowError):
        return False
    return gold["legacy_chunk_index"] == result_index


def _matched_gold_indices(
    gold_evidence: Sequence[dict],
    results: Sequence[dict],
    k: int,
    *,
    min_overlap_ratio: float,
) -> List[int]:
    matched = set()
    for row in list(results)[: max(0, int(k))]:
        for idx, gold in enumerate(gold_evidence):
            if idx in matched:
                continue
            if gold_matches_result(gold, row, min_overlap_ratio=min_overlap_ratio):
                matched.add(idx)
                break
    return sorted(matched)


def recall_at_k(
    gold_evidence: Sequence[dict],
    results: Sequence[dict],
    k: int,
    *,
    min_overlap_ratio: float = 0.5,
) -> float:
    """Compute gold-evidence recall at K for one query."""
    if not gold_evidence:
        return 0.0
    matched = _matched_gold_indices(
        gold_evidence, results, k, min_overlap_ratio=min_overlap_ratio
    )
    return len(matched) / float(len(gold_evidence))


def precision_at_k(
    gold_evidence: Sequence[dict],
    results: Sequence[dict],
    k: int,
    *,
    min_overlap_ratio: float = 0.5,
) -> float:
    """Compute the share of the top-K ranks containing relevant evidence."""
    limit = max(0, int(k))
    if not gold_evidence or limit <= 0:
        return 0.0
    relevant = sum(
        1
        for row in list(results)[:limit]
        if any(
            gold_matches_result(gold, row, min_overlap_ratio=min_overlap_ratio)
            for gold in gold_evidence
        )
    )
    return relevant / float(limit)


def mrr_at_k(
    gold_evidence: Sequence[dict],
    results: Sequence[dict],
    k: int,
    *,
    min_overlap_ratio: float = 0.5,
) -> float:
    """Compute reciprocal rank of the first gold hit within K."""
    if not gold_evidence:
        return 0.0
    for rank, row in enumerate(list(results)[: max(0, int(k))], start=1):
        if any(
            gold_matches_result(gold, row, min_overlap_ratio=min_overlap_ratio)
            for gold in gold_evidence
        ):
            return 1.0 / float(rank)
    return 0.0


def ndcg_at_k(
    gold_evidence: Sequence[dict],
    results: Sequence[dict],
    k: int,
    *,
    min_overlap_ratio: float = 0.5,
) -> float:
    """Compute binary nDCG at K, de-duplicating repeated hits to the same gold row."""
    if not gold_evidence:
        return 0.0

    seen_gold = set()
    dcg = 0.0
    for rank, row in enumerate(list(results)[: max(0, int(k))], start=1):
        matched_idx = None
        for idx, gold in enumerate(gold_evidence):
            if idx in seen_gold:
                continue
            if gold_matches_result(gold, row, min_overlap_ratio=min_overlap_ratio):
                matched_idx = idx
                break
        if matched_idx is None:
            continue
        seen_gold.add(matched_idx)
        dcg += 1.0 / math.log2(rank + 1)

    ideal_hits = min(len(gold_evidence), max(0, int(k)))
    if ideal_hits <= 0:
        return 0.0
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0.0 else 0.0


def score_query_results(
    query_case: dict, results: Sequence[dict], *, min_overlap_ratio: float = 0.5
) -> Dict[str, float]:
    """Score one query case against retrieved rows."""
    gold_evidence = query_case.get("gold_evidence") or []
    if not isinstance(gold_evidence, list):
        raise ValueError(
            f"{query_case.get('id', '<unknown>')} gold_evidence must be a list"
        )
    precision = partial(
        precision_at_k, gold_evidence, results, min_overlap_ratio=min_overlap_ratio
    )
    recall = partial(
        recall_at_k, gold_evidence, results, min_overlap_ratio=min_overlap_ratio
    )
    return {
        METRIC_PRECISION_1: precision(1),
        METRIC_PRECISION_5: precision(5),
        METRIC_PRECISION_10: precision(10),
        METRIC_RECALL_1: recall(1),
        METRIC_RECALL_5: recall(5),
        METRIC_RECALL_10: recall(10),
        METRIC_MRR_10: mrr_at_k(
            gold_evidence, results, 10, min_overlap_ratio=min_overlap_ratio
        ),
        METRIC_NDCG_10: ndcg_at_k(
            gold_evidence, results, 10, min_overlap_ratio=min_overlap_ratio
        ),
    }


def _mean(values: Iterable[float]) -> float:
    values = [float(value) for value in values]
    if not values:
        return 0.0
    return sum(values) / float(len(values))


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (percentile / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    fraction = position - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def aggregate_query_scores(
    query_scores: Sequence[dict],
    latencies_ms: Sequence[float],
    failed_query_count: int = 0,
) -> Dict[str, float]:
    """Aggregate per-query scores into benchmark-level metrics."""
    metrics = {
        metric: _mean(score.get(metric, 0.0) for score in query_scores)
        for metric in QUALITY_METRICS
    }
    metrics["mean_latency_ms"] = _mean(latencies_ms)
    metrics["p95_latency_ms"] = _percentile(latencies_ms, 95)
    metrics["failed_query_count"] = int(failed_query_count)
    return metrics


def relative_improvement(baseline: float, candidate: float) -> float:
    if baseline == 0.0:
        return math.inf if candidate > 0.0 else 0.0
    return (candidate - baseline) / abs(baseline)


def evaluate_thresholds(
    baseline_metrics: dict,
    candidate_metrics: dict,
    criteria: dict,
    *,
    query_count: int,
) -> dict:
    """Evaluate success criteria for a candidate run against a baseline run.

    Small checked-in fixture sets are not stable enough for relative percentage
    claims, so the function switches to configured absolute checks when the
    query count is below `min_queries_for_relative`.
    """
    min_queries = int(criteria.get("min_queries_for_relative", 20))
    small_sample = int(query_count) < min_queries
    relative_targets = criteria.get("relative_improvement") or {}
    absolute_targets = criteria.get("absolute_targets") or {}
    sample_absolute = criteria.get("sample_absolute") or absolute_targets
    latency_limit = float(criteria.get("max_p95_latency_regression", 0.20))
    allowed_failed = int(criteria.get("failed_query_count", 0))

    checks = []
    metrics_to_check = (METRIC_RECALL_5, METRIC_MRR_10, METRIC_NDCG_10)
    for metric in metrics_to_check:
        baseline_value = float(baseline_metrics.get(metric, 0.0))
        candidate_value = float(candidate_metrics.get(metric, 0.0))
        if small_sample:
            target = float(sample_absolute.get(metric, 0.0))
            passed = candidate_value >= target
            checks.append(
                {
                    "metric": metric,
                    "mode": "sample_absolute",
                    "baseline": baseline_value,
                    "candidate": candidate_value,
                    "target": target,
                    "passed": passed,
                }
            )
            continue

        absolute_target = float(absolute_targets.get(metric, 1.0))
        relative_target = float(relative_targets.get(metric, 0.0))
        improvement = relative_improvement(baseline_value, candidate_value)
        passed = candidate_value >= absolute_target or improvement >= relative_target
        checks.append(
            {
                "metric": metric,
                "mode": "relative_or_absolute",
                "baseline": baseline_value,
                "candidate": candidate_value,
                "absolute_target": absolute_target,
                "relative_target": relative_target,
                "relative_improvement": improvement,
                "passed": passed,
            }
        )

    baseline_latency = float(baseline_metrics.get("p95_latency_ms", 0.0))
    candidate_latency = float(candidate_metrics.get("p95_latency_ms", 0.0))
    max_latency = baseline_latency * (1.0 + latency_limit)
    latency_passed = baseline_latency <= 0.0 or candidate_latency <= max_latency
    checks.append(
        {
            "metric": "p95_latency_ms",
            "mode": "max_regression",
            "baseline": baseline_latency,
            "candidate": candidate_latency,
            "max_regression": latency_limit,
            "max_allowed": max_latency,
            "passed": latency_passed,
        }
    )

    failed_count = int(candidate_metrics.get("failed_query_count", 0))
    checks.append(
        {
            "metric": "failed_query_count",
            "mode": "exact_max",
            "candidate": failed_count,
            "max_allowed": allowed_failed,
            "passed": failed_count <= allowed_failed,
        }
    )

    return {
        "passed": all(bool(check["passed"]) for check in checks),
        "small_sample": small_sample,
        "min_queries_for_relative": min_queries,
        "checks": checks,
    }


def _quality_score(metrics: dict) -> float:
    return (
        (0.40 * float(metrics.get(METRIC_RECALL_5, 0.0)))
        + (0.35 * float(metrics.get(METRIC_MRR_10, 0.0)))
        + (0.25 * float(metrics.get(METRIC_NDCG_10, 0.0)))
    )


def select_optimized_run(run_summaries: Sequence[dict]) -> Optional[dict]:
    """Pick the strongest run by quality metrics, then lower latency."""
    candidates = []
    for run in run_summaries:
        metrics = run.get("metrics") or {}
        if int(metrics.get("failed_query_count", 0)) > 0:
            continue
        candidates.append(run)
    if not candidates:
        return None

    return max(
        candidates,
        key=lambda run: (
            _quality_score(run.get("metrics") or {}),
            -float((run.get("metrics") or {}).get("p95_latency_ms", 0.0)),
            str(run.get("name") or ""),
        ),
    )
