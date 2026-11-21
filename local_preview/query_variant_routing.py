"""Compare video candidates from the language variants of one query."""

from time import perf_counter
from typing import Callable

from multilingual.video_routing import RRF_K, video_relevance


def route_query_variants(
    expansion: dict, *, top_k: int, route_videos: Callable, clock: Callable = perf_counter
) -> dict:
    """Route each language scope and combine candidates by comparable evidence."""
    started_at = clock()
    compare_variants = len(expansion["variants"]) > 1
    fused = {}
    route_attempts = []
    for variant in expansion["variants"]:
        routed = route_videos(
            variant["query"],
            top_k=top_k,
            language=variant["language"],
            video_ids=variant["video_ids"],
            rank_by_relevance=compare_variants,
        )
        route_attempts.append(
            {
                "language": variant["language"],
                "kind": variant["kind"],
                "video_ids": list(routed.get("video_ids") or []),
                "used_fallback": bool(routed.get("used_fallback")),
                "fallback_reason": routed.get("fallback_reason"),
                "latency_ms": routed.get("latency_ms"),
            }
        )
        if routed.get("used_fallback"):
            continue
        for rank, candidate in enumerate(routed.get("results") or [], start=1):
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
            else:
                score = (1.0 / (RRF_K + rank)) + (0.02 * (lexical_score or 0.0))

            entry = fused.setdefault(
                video_id,
                {
                    "video_id": video_id,
                    "title": candidate.get("title"),
                    "language": candidate.get("language") or variant["language"],
                    "source": candidate.get("source"),
                    "score": score,
                    "variant_hits": [],
                },
            )
            entry["score"] = max(entry["score"], score)
            entry["variant_hits"].append(hit)

    ordered = sorted(
        fused.values(), key=lambda row: (-row["score"], row["video_id"])
    )[:top_k]
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
            "language_scoped_relevance" if compare_variants else "language_scoped_rrf"
        ),
        "route_attempts": route_attempts,
        "latency_ms": round((clock() - started_at) * 1000, 3),
    }
