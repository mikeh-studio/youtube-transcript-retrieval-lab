"""Section boundary and time coverage metrics for summary predictions."""

from collections import Counter
from math import ceil, fsum


def _boundaries(sections, duration):
    starts = sorted(section["start"] for section in sections)
    return [start for start in starts[1:] if 0 < start < duration]


def _matched(gold, predicted, tolerance):
    pairs = sorted(
        (abs(reference - candidate), gold_index, prediction_index)
        for gold_index, reference in enumerate(gold)
        for prediction_index, candidate in enumerate(predicted)
        if abs(reference - candidate) <= tolerance
    )
    used_gold, used_predictions = set(), set()
    for _, gold_index, prediction_index in pairs:
        if gold_index not in used_gold and prediction_index not in used_predictions:
            used_gold.add(gold_index)
            used_predictions.add(prediction_index)
    return len(used_gold)


def _window_diff_counts(gold, predicted, duration, section_count):
    bins = ceil(duration)
    k = min(bins - 1, max(1, int(duration / (2 * section_count))))
    window_count = bins - k if bins > 1 else 0
    if not window_count:
        return 0, 0

    # A boundary at b affects windows starting at i where i < b <= i+k.
    changes = Counter({0: 0, window_count: 0})
    for boundaries, sign in ((gold, 1), (predicted, -1)):
        for boundary in {int(seconds) for seconds in boundaries}:
            start, end = max(0, boundary - k), min(window_count, boundary)
            if start < end:
                changes[start] += sign
                changes[end] -= sign

    mismatch_count = difference = previous = 0
    for index, change in sorted(changes.items()):
        if difference:
            mismatch_count += index - previous
        difference += change
        previous = index
    return mismatch_count, window_count


def _covered_seconds(sections):
    last_end = 0
    covered = []
    for section in sorted(sections, key=lambda row: row["start"]):
        start, end = section["start"], section["end"]
        if end > last_end:
            covered.append(end - max(start, last_end))
            last_end = end
    return fsum(covered)


def section_counts(gold, prediction):
    """Count time coverage and boundary agreement where sections are available."""
    if "sections" not in prediction:
        return {}
    duration = gold["duration_s"]
    sections = prediction["sections"]
    counts = {
        "section_duration_s": duration,
        "section_covered_s": _covered_seconds(sections),
    }
    reference = gold.get("sections")
    if reference:
        expected = _boundaries(reference, duration)
        actual = _boundaries(sections, duration)
        errors, windows = _window_diff_counts(
            expected, actual, duration, len(reference)
        )
        counts.update(
            {
                "gold_internal_boundaries": len(expected),
                "predicted_internal_boundaries": len(actual),
                "boundary_hits_15s": _matched(expected, actual, 15),
                "boundary_hits_30s": _matched(expected, actual, 30),
                "window_diff_errors": errors,
                "window_diff_windows": windows,
                "gold_sections": len(reference),
                "predicted_sections": len(sections),
                "section_count_sources": 1,
                # Off when the plan has fewer than half or more than twice gold's sections.
                "section_count_off": int(
                    not len(reference) <= 2 * len(sections) <= 4 * len(reference)
                ),
            }
        )
    return counts


def section_metrics(counts):
    """Calculate ratios from counts that can be summed across episodes."""
    metrics = {}
    if "section_duration_s" in counts:
        metrics["section_coverage"] = (
            counts["section_covered_s"] / counts["section_duration_s"]
        )
    if "gold_internal_boundaries" in counts:
        total = (
            counts["gold_internal_boundaries"] + counts["predicted_internal_boundaries"]
        )
        for tolerance in (15, 30):
            hits = counts[f"boundary_hits_{tolerance}s"]
            metrics[f"boundary_f1@{tolerance}s"] = 2 * hits / total if total else 1.0
        windows = counts["window_diff_windows"]
        metrics["window_diff"] = (
            counts["window_diff_errors"] / windows if windows else None
        )
        metrics["section_count_ratio"] = (
            counts["predicted_sections"] / counts["gold_sections"]
        )
        metrics["section_count_off_rate"] = (
            counts["section_count_off"] / counts["section_count_sources"]
        )
    return metrics
