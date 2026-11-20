"""Deterministic summary metrics and per-system micro aggregates."""

import re
from collections import Counter
from math import fsum

from evals.contracts import ContractError
from evals.summary.predictions import RESOLVED_TIMESTAMP_SOURCES
from evals.summary.sections import section_counts, section_metrics


JP_SCRIPT = re.compile(r"[\u3041-\u30fa\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9d]")
LATIN = re.compile(r"[a-zA-Z]")


def detected_language(text):
    """Classify Japanese script first, then Latin script."""
    if JP_SCRIPT.search(text):
        return "ja"
    return "en" if LATIN.search(text) else None


def _span_candidate(point, start):
    return any(
        span["start"] - 30 <= start <= span["end"] + 30 for span in point["spans"]
    )


def _proxy_matches(points, items):
    """Find a stable maximum-cardinality pairing of span-compatible items."""
    candidates = {
        item["id"]: sorted(
            (point for point in points if _span_candidate(point, item["start"])),
            key=lambda point: (abs(point["onset"] - item["start"]), point["id"]),
        )
        for item in items
    }
    assigned = {}

    def assign(item_id, seen):
        for point in candidates[item_id]:
            point_id = point["id"]
            if point_id in seen:
                continue
            seen.add(point_id)
            if point_id not in assigned or assign(assigned[point_id], seen):
                assigned[point_id] = item_id
                return True
        return False

    for item in sorted(
        items, key=lambda item: (len(candidates[item["id"]]), item["id"])
    ):
        assign(item["id"], set())
    return {item_id: point_id for point_id, item_id in assigned.items()}


def _counts(gold, prediction, decisions):
    items = prediction["items"]
    points = gold["key_points"]
    points_by_id = {point["id"]: point for point in points}
    anchors = [
        item for item in items
        if item.get("timestamp_source") in RESOLVED_TIMESTAMP_SOURCES
    ]
    anchor_keys = {(item["start"], item["end"]) for item in anchors}
    matches = (
        _proxy_matches(points, items)
        if decisions is None
        else {
            item_id: row["decision"]
            for item_id, row in decisions["match"].items()
            if row["decision"] is not None
        }
    )
    errors = [
        abs(item["start"] - points_by_id[matches[item["id"]]]["onset"])
        for item in items
        if item["id"] in matches
    ]
    counts = Counter(
        {
            "sources": 1,
            "predictions": len(items),
            "gold_points": len(points),
            "known_anchor_sources": sum(
                item.get("timestamp_source") is not None for item in items
            ),
            "resolved_anchors": len(anchors),
            "duplicate_anchors": len(anchors) - len(anchor_keys),
            "timestamp_pairs": len(errors),
            "timestamp_hits_15s": sum(error <= 15 for error in errors),
            "timestamp_hits_30s": sum(error <= 30 for error in errors),
            "timestamp_error_sum_s": fsum(errors),
            "span_recalled": sum(
                any(_span_candidate(point, item["start"]) for item in items)
                for point in points
            ),
            "language_matches": sum(
                detected_language(item["text"]) == gold["language"] for item in items
            ),
        }
    )
    if decisions is not None:
        counts.update(
            {
                "gold_importance": sum(point["importance"] for point in points),
                "matched_importance": sum(
                    points_by_id[point_id]["importance"]
                    for point_id in matches.values()
                ),
                "supported": sum(
                    row["decision"] == "supported"
                    for row in decisions["faithfulness"].values()
                ),
                "partial": sum(
                    row["decision"] == "partial"
                    for row in decisions["faithfulness"].values()
                ),
            }
        )
    counts.update(section_counts(gold, prediction))
    return counts


def _metrics(counts, judged, window_policy, prompt_version):
    def rate(numerator, denominator):
        return counts[numerator] / counts[denominator] if counts[denominator] else None

    result = {
        "anchor_resolution_rate": rate("resolved_anchors", "known_anchor_sources"),
        "ts_hit@15s": rate("timestamp_hits_15s", "timestamp_pairs"),
        "ts_hit@30s": rate("timestamp_hits_30s", "timestamp_pairs"),
        "ts_mae_s": rate("timestamp_error_sum_s", "timestamp_pairs"),
        "keypoint_span_recall@30s": rate("span_recalled", "gold_points"),
        "duplicate_anchor_rate": rate("duplicate_anchors", "resolved_anchors"),
        "language_match_rate": rate("language_matches", "predictions"),
    }
    if judged:
        result.update(
            {
                "keypoint_recall_w": rate("matched_importance", "gold_importance"),
                "keypoint_precision": rate("timestamp_pairs", "predictions"),
                "faithfulness_rate": rate("supported", "predictions"),
                "partial_rate": rate("partial", "predictions"),
                "window_policy": window_policy,
                "prompt_version": prompt_version,
            }
        )
    result.update(section_metrics(counts))
    return result


def score(gold, predictions, judgments=None):
    """Score validated inputs with per-source counts and micro totals."""
    judged = judgments is not None
    policies = set()
    versions = set()
    for decisions in (judgments or {}).values():
        for row in decisions["faithfulness"].values():
            policies.add(row["window_policy"])
            versions.add(row["prompt_version"])
    if len(policies) > 1:
        raise ContractError(
            f"Cannot score mixed faithfulness window policies: {', '.join(sorted(policies))}"
        )
    if len(versions) > 1:
        raise ContractError(
            f"Cannot score mixed faithfulness prompt versions: {', '.join(sorted(versions))}"
        )
    window_policy = next(iter(policies), None)
    prompt_version = next(iter(versions), None)
    systems = {}
    totals = {}
    for (system, source_id), prediction in sorted(predictions.items()):
        decisions = judgments[system, source_id] if judged else None
        counts = _counts(gold[source_id], prediction, decisions)
        current = systems.setdefault(system, {"sources": {}})
        current["sources"][source_id] = {
            "language": gold[source_id]["language"],
            "provenance": {
                key: prediction.get(key)
                for key in ("provider", "model", "cache_hit", "language", "cli_version")
            },
            "counts": dict(counts),
            "metrics": _metrics(counts, judged, window_policy, prompt_version),
        }
        if judged and decisions["match"]:
            current["judge"] = next(iter(decisions["match"].values()))["judge"]
        totals.setdefault(system, Counter()).update(counts)
    for system, result in systems.items():
        result["aggregate"] = {
            "counts": dict(totals[system]),
            "metrics": _metrics(totals[system], judged, window_policy, prompt_version),
        }
    return {
        "schema": "summary-results/v1",
        "timestamp_pairing": "judge" if judged else "span_proxy",
        "systems": systems,
    }
