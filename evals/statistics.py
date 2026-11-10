"""Deterministic offline ranking metrics and query-level bootstrap intervals.

Result IDs are de-duplicated before ranks are assigned; unjudged IDs receive
zero relevance. The bootstrap uses percentile intervals with linear interpolation
and resamples queries, preserving pairing for comparisons.
"""

from collections.abc import Mapping, Sequence
import math
import random


def _id(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("IDs must be non-empty strings")
    return value


def score_ranking(relevance, results, *, unanswerable=False):
    """Score a single query, or return None for an explicitly unanswerable one.

    Relevance maps document IDs to integer grades 0, 1, or 2. Recall counts
    documents with grades above zero; nDCG uses gain 2**grade - 1.
    """
    if not isinstance(unanswerable, bool):
        raise ValueError("unanswerable must be a boolean")
    if not isinstance(relevance, Mapping):
        raise ValueError("relevance must be a mapping")
    grades = {}
    for key, grade in relevance.items():
        _id(key)
        if type(grade) is not int or grade not in (0, 1, 2):
            raise ValueError("relevance grades must be integers 0, 1, or 2")
        grades[key] = grade
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        raise ValueError("results must be a sequence of IDs")
    unique = []
    seen = set()
    for result in results:
        _id(result)
        if result not in seen:
            seen.add(result)
            unique.append(result)
    relevant_count = sum(grade > 0 for grade in grades.values())
    if unanswerable:
        if relevant_count:
            raise ValueError("unanswerable queries cannot have relevant documents")
        return None
    if not relevant_count:
        raise ValueError("answerable queries need at least one relevant document")
    ranked_grades = [grades.get(item, 0) for item in unique[:10]]
    recall5 = sum(grade > 0 for grade in ranked_grades[:5]) / relevant_count
    recall10 = sum(grade > 0 for grade in ranked_grades) / relevant_count
    mrr = next((1 / rank for rank, grade in enumerate(ranked_grades, 1) if grade), 0.0)
    dcg = sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(ranked_grades, 1)
    )
    ideal = sorted(grades.values(), reverse=True)[:10]
    idcg = sum(
        (2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, 1)
    )
    return {
        "recall@5": recall5,
        "recall@10": recall10,
        "mrr@10": mrr,
        "ndcg@10": dcg / idcg,
    }


def _numbers(values):
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or not values
    ):
        raise ValueError("values must be a non-empty sequence")
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("values must be finite numbers")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValueError("values must be finite numbers") from exc
        if not math.isfinite(number):
            raise ValueError("values must be finite numbers")
        result.append(number)
    return result


def _percentile(ordered, fraction):
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap_mean_ci(values, *, seed=0, samples=10000):
    """Return mean and a seeded, two-sided 95% percentile bootstrap interval."""
    numbers = _numbers(values)
    if type(seed) is not int or type(samples) is not int or samples < 1:
        raise ValueError("seed must be an integer and samples a positive integer")
    rng = random.Random(seed)
    n = len(numbers)
    # fsum improves accuracy and its overflow is surfaced as invalid input.
    try:
        mean = math.fsum(value / n for value in numbers)
        estimates = sorted(
            math.fsum(numbers[rng.randrange(n)] / n for _ in range(n))
            for _ in range(samples)
        )
    except OverflowError as exc:
        raise ValueError("values produce a non-finite statistic") from exc
    if not math.isfinite(mean) or not all(math.isfinite(x) for x in estimates):
        raise ValueError("values produce a non-finite statistic")
    return {
        "mean": mean,
        "lower": _percentile(estimates, 0.025),
        "upper": _percentile(estimates, 0.975),
        "n": n,
    }


def _paired_values(rows):
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise ValueError("paired values must be a non-empty sequence")
    parsed = {}
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise ValueError("paired rows must contain query ID and value")
        query_id = _id(row[0])
        if query_id in parsed:
            raise ValueError("duplicate query ID")
        parsed[query_id] = _numbers([row[1]])[0]
    return parsed


def bootstrap_paired_delta_ci(baseline, candidate, *, seed=0, samples=10000):
    """Bootstrap candidate minus baseline on the same uniquely identified queries."""
    left, right = _paired_values(baseline), _paired_values(candidate)
    if left.keys() != right.keys():
        raise ValueError("baseline and candidate query IDs must match")
    return bootstrap_mean_ci(
        [right[key] - left[key] for key in sorted(left)], seed=seed, samples=samples
    )
