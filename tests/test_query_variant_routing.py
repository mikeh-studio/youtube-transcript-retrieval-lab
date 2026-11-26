"""Check the language-variant routing merge and its optional gates."""

from local_preview import query_variant_routing
from local_preview.query_variant_routing import route_query_variants


EXPANSION = {"variants": [
    {"query": "jazz recap", "language": "en", "kind": "original", "video_ids": ["a", "b", "c"]},
    {"query": "ジャズ", "language": "ja", "kind": "translated", "video_ids": ["j"]},
]}
SCORES = {"en": [("a", 0.87), ("b", 0.869), ("c", 0.84)], "ja": [("j", 0.849)]}


def _route(query, *, top_k, language, video_ids, rank_by_relevance):
    assert rank_by_relevance
    return {"video_ids": [v for v, _ in SCORES[language]], "results": [
        {"video_id": v, "dense_score": s, "lexical_score": 0.0} for v, s in SCORES[language]
    ][:top_k]}


def _ids(**kwargs):
    kwargs.setdefault("pool_normalization", "none")
    return route_query_variants(EXPANSION, top_k=3, route_videos=_route, **kwargs)["video_ids"]


def test_raw_merge_lets_translated_hit_displace_original(monkeypatch):
    monkeypatch.delenv(query_variant_routing.TRANSLATED_PENALTY_ENV, raising=False)
    monkeypatch.delenv(query_variant_routing.MAX_SCORE_GAP_ENV, raising=False)
    assert _ids() == ["a", "b", "j"]


def test_pool_median_is_the_default_and_can_be_turned_off(monkeypatch):
    monkeypatch.delenv(query_variant_routing.POOL_NORMALIZATION_ENV, raising=False)
    routed = route_query_variants(EXPANSION, top_k=3, route_videos=_route)
    assert routed["fusion"] == "language_pool_median"
    monkeypatch.setenv(query_variant_routing.POOL_NORMALIZATION_ENV, "none")
    routed = route_query_variants(EXPANSION, top_k=3, route_videos=_route)
    assert routed["fusion"] == "language_scoped_relevance"


def test_translated_penalty_keeps_original_language_candidate(monkeypatch):
    assert _ids(translated_penalty=0.02) == ["a", "b", "c"]
    monkeypatch.setenv(query_variant_routing.TRANSLATED_PENALTY_ENV, "0.02")
    assert _ids() == ["a", "b", "c"]


def test_score_gap_drops_weak_candidates(monkeypatch):
    monkeypatch.delenv(query_variant_routing.TRANSLATED_PENALTY_ENV, raising=False)
    assert _ids(max_score_gap=0.01) == ["a", "b"]
    assert _ids(max_score_gap=0.05) == ["a", "b", "j"]


POOL_EXPANSION = {"variants": [
    {"query": "jazz recap", "language": "en", "kind": "original",
     "video_ids": ["a", "b", "c", "d"]},
    {"query": "ジャズ", "language": "ja", "kind": "translated", "video_ids": ["j", "k", "l"]},
]}
# English has two clear matches; Japanese is flat, so its best hit is not distinctive.
POOL_SCORES = {"en": [("a", 0.90), ("b", 0.88), ("c", 0.80), ("d", 0.79)],
               "ja": [("j", 0.86), ("k", 0.855), ("l", 0.85)]}


def _pool_route(query, *, top_k, language, video_ids, rank_by_relevance):
    rows = POOL_SCORES[language][:top_k]
    return {"video_ids": [v for v, _ in rows], "results": [
        {"video_id": v, "dense_score": s, "lexical_score": 0.0} for v, s in rows
    ]}


def test_pool_normalization_ranks_against_each_language_pool(monkeypatch):
    for name in (query_variant_routing.TRANSLATED_PENALTY_ENV,
                 query_variant_routing.MAX_SCORE_GAP_ENV,
                 query_variant_routing.POOL_NORMALIZATION_ENV):
        monkeypatch.delenv(name, raising=False)

    def ids(**kwargs):
        return route_query_variants(POOL_EXPANSION, top_k=3, route_videos=_pool_route,
                                    **kwargs)

    assert ids(pool_normalization="none")["video_ids"] == ["a", "b", "j"]
    zscore = ids(pool_normalization="zscore")
    # A nearly flat pool has a tiny spread, so z-scores inflate its best hit.
    assert zscore["video_ids"] == ["a", "j", "b"]
    assert zscore["fusion"] == "language_pool_zscore"
    assert zscore["route_attempts"][0]["video_ids"] == ["a", "b", "c"]
    assert ids(pool_normalization="median")["video_ids"] == ["a", "b", "j"]
    assert query_variant_routing.normalize_pool({"x": 1.0, "y": 1.0}, "zscore") == {
        "x": 0.0, "y": 0.0}


def test_unknown_pool_normalization_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="pool normalization"):
        route_query_variants(POOL_EXPANSION, top_k=3, route_videos=_pool_route,
                             pool_normalization="minmax")


def test_penalty_spares_the_best_candidate(monkeypatch):
    monkeypatch.delenv(query_variant_routing.PENALTY_SPARES_BEST_ENV, raising=False)
    expansion = {"variants": [
        {"query": "q", "language": "ja", "kind": "original", "video_ids": ["a", "b", "c"]},
        {"query": "q", "language": "en", "kind": "translated", "video_ids": ["e", "f"]},
    ]}
    scores = {"ja": [("a", 0.845), ("b", 0.842), ("c", 0.840)],
              "en": [("e", 0.850), ("f", 0.839)]}

    def route(query, *, top_k, language, video_ids, rank_by_relevance):
        return {"video_ids": [v for v, _ in scores[language]], "results": [
            {"video_id": v, "dense_score": s, "lexical_score": 0.0}
            for v, s in scores[language]][:top_k]}

    def ids(**kwargs):
        return route_query_variants(expansion, top_k=3, route_videos=route,
                                    translated_penalty=0.02, pool_normalization="none",
                                    **kwargs)["video_ids"]

    assert ids() == ["a", "b", "c"]
    assert ids(penalty_spares_best=True) == ["e", "a", "b"]
