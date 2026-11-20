"""Score human-reviewed predictions, citation resolution, and pairwise choices."""

from collections import Counter


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def score_digest(digest=None, judgments=(), preferences=()):
    """Return rates and denominators; use None when a metric has no observations."""
    judgments = list(judgments)
    reviewed = Counter()
    approved = Counter()
    for row in judgments:
        if row.get("task") != "digest_novelty" or row.get("relation") not in (
            "new",
            "contradicts",
        ):
            raise ValueError("Expected finalized digest_novelty label rows")
        if (
            digest is not None
            and row.get("label_provenance", {}).get("digest_id") != digest["digest_id"]
        ):
            raise ValueError("Novelty labels must belong to the supplied digest")
        decision = row.get("review_decision")
        if decision not in ("approved", "rejected"):
            raise ValueError("A novelty label must be approved or rejected")
        relation = row["relation"]
        reviewed[relation] += 1
        approved[relation] += decision == "approved"

    if digest is None:
        evidence_rows = judgments
    else:
        evidence_rows = [
            claim for release in digest["releases"] for claim in release["claims"]
        ]
    resolved = submitted = 0
    for row in evidence_rows:
        available = {passage["passage_id"] for passage in row["prior_passages"]}
        prior = row["prior"]
        count = row["submitted_prior_count"]
        if type(count) is not int or count < len(prior):
            raise ValueError("Invalid submitted prior citation count")
        submitted += count
        resolved += len(available.intersection(prior))

    choices = Counter()
    for row in preferences:
        choice = row.get("choice")
        if choice not in ("digest", "baseline", "tie"):
            raise ValueError("Pairwise choice must be digest, baseline, or tie")
        choices[choice] += 1
    votes = sum(choices.values())
    return {
        "novelty_precision": _ratio(approved["new"], reviewed["new"]),
        "contradiction_precision": _ratio(
            approved["contradicts"], reviewed["contradicts"]
        ),
        "prior_evidence_validity": _ratio(resolved, submitted),
        "preference_win_rate": _ratio(choices["digest"] + 0.5 * choices["tie"], votes),
        "counts": {
            "novelty_reviewed": reviewed["new"],
            "novelty_approved": approved["new"],
            "contradiction_reviewed": reviewed["contradicts"],
            "contradiction_approved": approved["contradicts"],
            "prior_citations": submitted,
            "resolved_prior_citations": resolved,
            "preference_votes": votes,
            "preference_digest_wins": choices["digest"],
            "preference_ties": choices["tie"],
        },
    }
