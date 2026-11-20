"""Generate and score cross-source predictions with injected systems and judges."""

import json
import math

from evals.contracts import ContractError
from evals.evidence_spans import normalize_gold_evidence, span_matches, validate_overlap_ratio
from evals.summary.dataset import by_key, objects
from evals.summary.judge import DATA_RULE, decode_judge_reply


JUDGE_TASK = """Match each predicted theme to the gold theme with the same substantive
topic, or null. Match each gold theme at most once. Independently assess every
perspective. A true decision requires the cited evidence from its declared
source to support the statement and its stance in the context of the other
perspectives for that theme. Treat missing citation text as unsupported. For
only_here, assess uniqueness within the supplied evidence. Do not infer global
absence. Return JSON with theme_matches (an array of {id, gold_id}) and stances
(an array of {id, supported}). Include each predicted theme and perspective ID
exactly once. Use a JSON boolean for supported."""


def generate_ask_prediction(case, answer_fn, *, provider=None, model=None):
    """Call an injected /v1/ask adapter with library-wide multi-vector routing."""
    request = {
        "question": case["question"],
        "language": case["language"],
        "video_routing": "multi_vector",
    }
    if provider is not None:
        request["provider"] = provider
    if model is not None:
        request["model"] = model
    return answer_fn(**request)


def generate_collection_prediction(case, collection_fn, *, provider, model, focus=None):
    """Call an injected collection-summary adapter using the label's selected scope."""
    return collection_fn(
        source_ids=list(case["selected_source_ids"]),
        language=case["language"],
        focus=case.get("question") if focus is None else focus,
        provider=provider,
        model=model,
    )


def _gold_spans(rows):
    unique = {}
    for row in rows:
        span = normalize_gold_evidence(row)
        if "start" not in span or span["start"] < 0:
            raise ContractError("Cross-source gold evidence requires timestamp spans")
        unique[(span["video_id"], span["start"], span["end"])] = span
    return list(unique.values())


def _timestamped_citation(row):
    if not isinstance(row, dict):
        return False
    source_id = _source_id(row)
    bounds = row.get("timestamp_range", row)
    if not isinstance(bounds, dict) or not isinstance(source_id, str) or not source_id:
        return False
    start = bounds.get("start", bounds.get("start_seconds"))
    end = bounds.get("end", bounds.get("end_seconds"))
    return (
        type(start) in (int, float) and type(end) in (int, float)
        and 0 <= start <= end < math.inf
    )


def _source_id(row):
    return row.get("video_id") or row.get("source_id")


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def answer_source_coverage(gold_spans, citations):
    """Return the fraction of gold sources with at least one timestamped citation."""
    expected = {row["video_id"] for row in _gold_spans(gold_spans)}
    observed = {_source_id(row) for row in citations if _timestamped_citation(row)}
    return _rate(len(expected & observed), len(expected))


def answer_span_recall(gold_spans, citations, *, min_overlap_ratio=0.5):
    """Return the fraction of gold spans hit by same-source citations using P1."""
    ratio = validate_overlap_ratio(min_overlap_ratio)
    gold = _gold_spans(gold_spans)
    evidence = [row for row in citations if _timestamped_citation(row)]
    hits = sum(
        any(row["video_id"] == _source_id(candidate) and span_matches(row, candidate, ratio)
            for candidate in evidence)
        for row in gold
    )
    return _rate(hits, len(gold))


def _evidence_scores(gold_spans, citations, min_overlap_ratio):
    return {
        "answer_source_coverage": answer_source_coverage(gold_spans, citations),
        "answer_span_recall": answer_span_recall(
            gold_spans, citations, min_overlap_ratio=min_overlap_ratio
        ),
    }


def score_ask(gold_case, prediction, *, min_overlap_ratio=0.5):
    """Score issued Ask citations, excluding retrieval candidates and abstentions."""
    status = prediction.get("status")
    if prediction.get("ok") is False or status not in ("answered", "insufficient_evidence"):
        raise ContractError("Expected a successful or abstaining Ask response")
    citations = prediction["citations"] if status == "answered" else []
    return _evidence_scores(gold_case["gold_spans"], citations, min_overlap_ratio)


def _collection_evidence(prediction):
    selected = set(prediction["source_ids"])
    catalog = by_key(
        objects(prediction["evidence_catalog"], "collection evidence catalog"),
        "citation_id", "collection citation",
    )
    claims, cited = [], []
    for theme_number, theme in enumerate(objects(prediction["themes"], "themes"), 1):
        theme_id = f"T{theme_number}"
        perspectives = []
        for number, item in enumerate(objects(theme["perspectives"], "perspectives"), 1):
            evidence = []
            source_id = item["source_id"]
            if source_id in selected:
                for ref in item["citations"]:
                    row = catalog.get(ref) if isinstance(ref, str) else None
                    if _timestamped_citation(row) and _source_id(row) == source_id:
                        cited.append(row)
                        excerpt = row.get("text") or row.get("snippet")
                        if isinstance(excerpt, str) and excerpt.strip():
                            evidence.append(excerpt)
            perspectives.append({
                "id": f"{theme_id}.P{number}",
                "source_id": source_id,
                "stance": item["stance"],
                "statement": item["statement"],
                "evidence": evidence,
            })
        claims.append({
            "id": theme_id, "title": theme["title"], "summary": theme["summary"],
            "perspectives": perspectives,
        })
    return claims, cited


def _semantic_scores(gold, claims, llm):
    gold_by_id = by_key(gold, "id", "gold theme")
    theme_ids = {theme["id"] for theme in claims}
    supports = {
        p["id"]: bool(p["evidence"]) for theme in claims for p in theme["perspectives"]
    }
    if not claims:
        return {"theme_recall": _rate(0, len(gold_by_id)), "stance_source_precision": None}

    response = llm(
        system_prompt=DATA_RULE + JUDGE_TASK,
        user_message=json.dumps({
            "gold_themes": [{key: row[key] for key in ("id", "title", "source_ids")}
                            for row in gold],
            "predicted_themes": claims,
        }, ensure_ascii=False),
    )
    try:
        decoded = decode_judge_reply(response["text"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("Cross-source judge must return valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ContractError("Cross-source judgment must be an object")

    matches = by_key(objects(decoded.get("theme_matches"), "theme matches"), "id", "match")
    stances = by_key(objects(decoded.get("stances"), "stance judgments"), "id", "stance")
    if matches.keys() != theme_ids or stances.keys() != supports.keys():
        raise ContractError("Cross-source judge must decide every theme and perspective")
    used = set()
    for row in matches.values():
        match = row.get("gold_id")
        if "gold_id" not in row or (match is not None and (
            not isinstance(match, str) or match not in gold_by_id or match in used
        )):
            raise ContractError("Theme matches must use known gold IDs one-to-one")
        if match is not None:
            used.add(match)
    for row in stances.values():
        if type(row.get("supported")) is not bool:
            raise ContractError("Each stance decision must be a boolean")
        if row["supported"] and not supports[row["id"]]:
            raise ContractError("A supported stance requires cited evidence from its source")
    return {
        "theme_recall": _rate(len(used), len(gold_by_id)),
        "stance_source_precision": _rate(
            sum(row["supported"] for row in stances.values()), len(stances)
        ),
    }


def score_collection(
    prediction, *, gold_spans=(), gold_themes=(), judge_llm=None, min_overlap_ratio=0.5
):
    """Score linked citations and optionally judge theme recall and source stances."""
    if prediction.get("ok") is False:
        raise ContractError("Expected a successful collection summary response")
    claims, citations = _collection_evidence(prediction)
    return {
        **_evidence_scores(gold_spans, citations, min_overlap_ratio),
        **(
            _semantic_scores(list(gold_themes), claims, judge_llm)
            if judge_llm is not None
            else {"theme_recall": None, "stance_source_precision": None}
        ),
    }
