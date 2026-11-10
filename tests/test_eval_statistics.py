import math

import pytest

from evals.statistics import bootstrap_mean_ci, bootstrap_paired_delta_ci, score_ranking


def test_graded_metrics_deduplicate_before_ranking():
    grades = {"a": 2, "b": 1, "c": 1}
    result = score_ranking(grades, ["miss", "b", "b", "a", "x", "y", "c"])
    assert result["recall@5"] == pytest.approx(2 / 3)
    assert result["recall@10"] == 1
    assert result["mrr@10"] == 0.5
    dcg = 1 / math.log2(3) + 3 / math.log2(4) + 1 / math.log2(7)
    ideal = 3 + 1 / math.log2(3) + 1 / math.log2(4)
    assert result["ndcg@10"] == pytest.approx(dcg / ideal)


def test_cutoffs_and_unanswerable_separation():
    result = score_ranking({"a": 1, "b": 2}, [str(i) for i in range(9)] + ["a", "b"])
    assert result["recall@5"] == 0
    assert result["recall@10"] == 0.5
    assert result["mrr@10"] == 0.1
    assert score_ranking({"a": 0}, ["a"], unanswerable=True) is None
    with pytest.raises(ValueError):
        score_ranking({}, [])
    with pytest.raises(ValueError):
        score_ranking({"a": 1}, [], unanswerable=True)


@pytest.mark.parametrize(
    "grades, results",
    [
        ({"a": 3}, []),
        ({"a": True}, []),
        ({"a": float("nan")}, []),
        ({"": 1}, []),
        ({"a": 1}, "a"),
        ({"a": 1}, [None]),
    ],
)
def test_invalid_ranking_inputs(grades, results):
    with pytest.raises(ValueError):
        score_ranking(grades, results)


def test_bootstrap_constant_and_deterministic():
    assert bootstrap_mean_ci([2, 2, 2], seed=7, samples=100) == {
        "mean": 2,
        "lower": 2,
        "upper": 2,
        "n": 3,
    }
    result = bootstrap_mean_ci([0, 1, 2], seed=7, samples=100)
    assert result == bootstrap_mean_ci([0, 1, 2], seed=7, samples=100)
    assert result["mean"] == 1
    assert 0 <= result["lower"] <= result["upper"] <= 2


def test_paired_delta_aligns_ids_and_preserves_pairing():
    baseline = [("a", 0), ("b", 100)]
    candidate = [("b", 101), ("a", 1)]
    assert bootstrap_paired_delta_ci(baseline, candidate, samples=100) == {
        "mean": 1,
        "lower": 1,
        "upper": 1,
        "n": 2,
    }
    with pytest.raises(ValueError):
        bootstrap_paired_delta_ci(baseline, [("a", 1), ("a", 2)])
    with pytest.raises(ValueError):
        bootstrap_paired_delta_ci(baseline, [("a", 1), ("c", 2)])


@pytest.mark.parametrize("values", [[], [float("nan")], [float("inf")], [True], ["1"]])
def test_invalid_bootstrap_values(values):
    with pytest.raises(ValueError):
        bootstrap_mean_ci(values, samples=10)


def test_invalid_bootstrap_parameters():
    for kwargs in ({"samples": 0}, {"samples": True}, {"seed": 0.5}):
        with pytest.raises(ValueError):
            bootstrap_mean_ci([1], **kwargs)
