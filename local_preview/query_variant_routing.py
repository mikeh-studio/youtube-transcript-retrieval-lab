"""Compare video candidates from the language variants of one query."""

import os
from statistics import mean, median, pstdev
from time import perf_counter
from typing import Callable, Optional

from multilingual.video_routing import RRF_K, video_relevance


def _env_float(name: str) -> Optional[float]:
    value = os.environ.get(name, "").strip()
    return float(value) if value else None


# Routing gates. The penalty and gap are experimental and off unless set; pool
# normalization defaults to the median and is turned off with "none".
TRANSLATED_PENALTY_ENV = "YT_RAG_ROUTING_TRANSLATED_PENALTY"
MAX_SCORE_GAP_ENV = "YT_RAG_ROUTING_MAX_SCORE_GAP"
POOL_NORMALIZATION_ENV = "YT_RAG_ROUTING_POOL_NORMALIZATION"
PENALTY_SPARES_BEST_ENV = "YT_RAG_ROUTING_PENALTY_SPARES_BEST"
POOL_NORMALIZATIONS = ("zscore", "median")
DEFAULT_POOL_NORMALIZATION = "median"
POOL_NORMALIZATION_OFF = ("none", "off")
MIN_POOL_STDEV = 0.005


def normalize_pool(scores: dict, method: str) -> dict:
    """Rescore one language pool relative to its own score distribution."""
    if not scores:
        return {}
    values = list(scores.values())
    if method == "median":
        center = median(values)
        return {key: value - center for key, value in scores.items()}
    center, spread = mean(values), max(pstdev(values), MIN_POOL_STDEV)
    return {key: (value - center) / spread for key, value in scores.items()}


def route_query_variants(
    expansion: dict,
    *,
    top_k: int,
    route_videos: Callable,
    clock: Callable = perf_counter,
    translated_penalty: Optional[float] = None,
    max_score_gap: Optional[float] = None,
    pool_normalization: Optional[str] = None,
    penalty_spares_best: Optional[bool] = None,
) -> dict:
    """Route each language scope and combine candidates by comparable evidence.

    When several language variants compete, ``translated_penalty`` lowers the
    relevance of hits found only through a translated query, and ``max_score_gap``
    drops candidates scoring more than that below the best one.
    ``pool_normalization`` (``median`` by default, ``zscore``, or ``none``) scores every candidate
    against the other sources in its own language pool before merging, so the
    best match from an unrelated pool no longer looks as strong as a real match.
    With ``penalty_spares_best``, the single best candidate before the penalty
    keeps its unpenalized score, so a clear cross-language match is never dropped.
    """
    started_at = clock()
    if translated_penalty is None:
        translated_penalty = _env_float(TRANSLATED_PENALTY_ENV) or 0.0
    if max_score_gap is None:
        max_score_gap = _env_float(MAX_SCORE_GAP_ENV)
    if pool_normalization is None:
        pool_normalization = (
            os.environ.get(POOL_NORMALIZATION_ENV, "").strip().lower()
            or DEFAULT_POOL_NORMALIZATION
        )
    if pool_normalization in POOL_NORMALIZATION_OFF:
        pool_normalization = None
    if pool_normalization not in (None, *POOL_NORMALIZATIONS):
        raise ValueError(f"Unknown routing pool normalization: {pool_normalization!r}")
    if penalty_spares_best is None:
        penalty_spares_best = os.environ.get(PENALTY_SPARES_BEST_ENV, "").strip().lower() in (
            "1", "true", "yes", "on")
    compare_variants = len(expansion["variants"]) > 1
    normalize = compare_variants and pool_normalization is not None
    fused = {}
    route_attempts = []
    for variant in expansion["variants"]:
        routed = route_videos(
            variant["query"],
            # A pool's distribution needs every source in it, not only its top hits.
            top_k=max(top_k, len(variant["video_ids"])) if normalize else top_k,
            language=variant["language"],
            video_ids=variant["video_ids"],
            rank_by_relevance=compare_variants,
        )
        route_attempts.append(
            {
                "language": variant["language"],
                "kind": variant["kind"],
                "video_ids": list(routed.get("video_ids") or [])[:top_k],
                "used_fallback": bool(routed.get("used_fallback")),
                "fallback_reason": routed.get("fallback_reason"),
                "latency_ms": routed.get("latency_ms"),
            }
        )
        if routed.get("used_fallback"):
            continue
        candidates = routed.get("results") or []
        pool_scores = (
            normalize_pool(
                {
                    row["video_id"]: video_relevance(
                        row.get("dense_score"), row.get("lexical_score")
                    )
                    for row in candidates
                },
                pool_normalization,
            )
            if normalize
            else {}
        )
        for rank, candidate in enumerate(candidates, start=1):
            video_id = candidate["video_id"]
            dense_score = candidate.get("dense_score")
            lexical_score = candidate.get("lexical_score")
            hit = {
                "language": variant["language"],
                "kind": variant["kind"],
                "rank": rank,
                "dense_score": dense_score,
                "lexical_score": lexical_score,
            }
            if compare_variants:
                score = video_relevance(dense_score, lexical_score)
                hit["relevance_score"] = score
                if normalize:
                    score = pool_scores[video_id]
                    hit["pool_score"] = score
                unpenalized = score
                if variant["kind"] == "translated":
                    score -= translated_penalty
            else:
                score = (1.0 / (RRF_K + rank)) + (0.02 * (lexical_score or 0.0))
                unpenalized = score

            entry = fused.setdefault(
                video_id,
                {
                    "video_id": video_id,
                    "title": candidate.get("title"),
                    "language": candidate.get("language") or variant["language"],
                    "source": candidate.get("source"),
                    "score": score,
                    "unpenalized_score": unpenalized,
                    "raw_relevance": hit.get("relevance_score", 0.0),
                    "variant_hits": [],
                },
            )
            entry["score"] = max(entry["score"], score)
            entry["unpenalized_score"] = max(entry["unpenalized_score"], unpenalized)
            entry["raw_relevance"] = max(entry["raw_relevance"], hit.get("relevance_score", 0.0))
            entry["variant_hits"].append(hit)

    if compare_variants and penalty_spares_best and translated_penalty and fused:
        best = min(fused.values(), key=lambda row: (-row["unpenalized_score"], row["video_id"]))
        best["score"] = best["unpenalized_score"]
    # Pool scores tie when sources sit at their pool's median, so raw relevance
    # breaks ties before the video id does.
    ordered = sorted(
        fused.values(),
        key=lambda row: (-row["score"], -row["raw_relevance"], row["video_id"]),
    )[:top_k]
    for row in ordered:
        row.pop("unpenalized_score", None)
        row.pop("raw_relevance", None)
    if compare_variants and max_score_gap is not None and ordered:
        floor = ordered[0]["score"] - max_score_gap
        ordered = [row for row in ordered if row["score"] >= floor]
    for rank, candidate in enumerate(ordered, start=1):
        candidate["rank"] = rank
    fallback_reason = None
    if not ordered:
        fallback_reason = next(
            (
                attempt["fallback_reason"]
                for attempt in route_attempts
                if attempt.get("fallback_reason")
            ),
            "no_language_variant_signal",
        )
    hits = [hit for candidate in ordered for hit in candidate["variant_hits"]]
    return {
        "video_ids": [candidate["video_id"] for candidate in ordered],
        "results": ordered,
        "used_fallback": not bool(ordered),
        "fallback_reason": fallback_reason,
        "dense_available": any(hit["dense_score"] is not None for hit in hits),
        "lexical_available": any(hit["lexical_score"] is not None for hit in hits),
        "fusion": (
            f"language_pool_{pool_normalization}"
            if normalize
            else "language_scoped_relevance"
            if compare_variants
            else "language_scoped_rrf"
        ),
        "route_attempts": route_attempts,
        "latency_ms": round((clock() - started_at) * 1000, 3),
    }
