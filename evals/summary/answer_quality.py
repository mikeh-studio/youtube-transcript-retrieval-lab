"""Score grounded Ask answers: claim support, citation precision, facts and abstention."""

import json
import random
import re

from evals.contracts import ContractError, file_sha256
from evals.summary.dataset import by_key, objects, read_jsonl, require_input_path, text
from evals.summary.judge import DATA_RULE, decode_judge_reply
from evals.summary.predictions import record_sha256


PROMPT_VERSION = "answer_quality_v1"
CLAIM_LABELS = ("supported", "partial", "unsupported", "no_claim")
STATUSES = ("answered", "insufficient_evidence")
MAX_JUDGE_ATTEMPTS = 2
AUDIT_SEED = 42
AUDIT_SIZE = 20
MARKER_RE = re.compile(r"\[(\d+)\]")
# Markers written after the sentence's closing punctuation still belong to that sentence.
TRAILING_MARKERS_RE = re.compile(r"([.!?。！？])((?:\s*\[\d+\])+)")
ABBREVIATIONS = {"jr", "sr", "dr", "mr", "mrs", "ms", "st", "vs", "no", "etc", "e.g", "i.e"}

JUDGE_TASK = (
    "You grade one answer to a question about recorded conversations. "
    "Part 1, facts: for each required fact, decide whether the answer text conveys it. "
    "Judge meaning, not wording, and ignore citations for this part. A fact the answer "
    "says is missing from the evidence is not conveyed. "
    "Part 2, claims: grade each answer claim only against the excerpts it cites. "
    "Do not use outside knowledge or excerpts cited by other claims. "
    "Check every substantive detail: people, works, numbers, events, causes and comparisons. "
    "Ignore who said something and minor transcription variants of names. "
    "Use supported if the cited excerpts establish every substantive detail, partial if "
    "they establish some, unsupported if they establish none or contradict it, and "
    "no_claim if the text only says that the evidence is missing or does not establish "
    "something. List in supporting_citations the cited IDs whose excerpt establishes at "
    "least part of the claim; it is empty for unsupported and no_claim, and nonempty for "
    "supported and partial. "
    'Return JSON {"facts":[{"id":"F1","conveyed":true}],'
    '"claims":[{"id":"C1","decision":"supported","supporting_citations":[1]}]}. '
    "Include every fact and claim exactly once. Use JSON booleans and integer IDs."
)


def load_qa_gold(paths):
    """Load approved multi-source QA labels after verifying each manifest digest."""
    gold = {}
    for path in paths:
        require_input_path(path)
        manifest_path = path.with_name(path.name[: -len(".jsonl")] + ".manifest.json")
        require_input_path(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("task") != "multi_source_qa":
            raise ContractError(f"{path.name} is not a multi_source_qa dataset")
        if manifest.get("sha256") != file_sha256(path):
            raise ContractError(f"{path.name} does not match its manifest digest")
        dataset_id = text(manifest.get("dataset_id"), "manifest.dataset_id")
        for row in read_jsonl(path):
            if row.get("review_decision") not in ("approved", "edited"):
                continue
            item_id = text(row.get("id"), "gold.id")
            text(row.get("question"), "gold.question")
            facts = row.get("required_facts")
            if not isinstance(facts, list) or any(
                not isinstance(fact, str) or not fact.strip() for fact in facts
            ):
                raise ContractError("gold.required_facts must be a list of text")
            answerable = row.get("answerable", True)
            if type(answerable) is not bool or (answerable and not facts):
                raise ContractError("An answerable question requires required_facts")
            sources = row.get("selected_source_ids")
            if not isinstance(sources, list) or not sources:
                raise ContractError("gold.selected_source_ids must be a nonempty list")
            gold[f"{dataset_id}/{item_id}"] = {
                **row,
                "answerable": answerable,
                "dataset_id": dataset_id,
                "case_id": f"{dataset_id}/{item_id}",
            }
    if not gold:
        raise ContractError("No approved multi-source QA labels were supplied")
    return gold


def _gold_index(gold):
    index = {}
    for case_id, row in gold.items():
        key = (row["id"], tuple(row["selected_source_ids"]))
        if key in index:
            raise ContractError("Gold labels are ambiguous for prediction matching")
        index[key] = case_id
    return index


def load_ask_predictions(path, gold):
    """Load saved Ask rows and bind each one to exactly one gold case."""
    index = _gold_index(gold)
    predictions = {}
    for row in read_jsonl(path):
        key = (row.get("label_id"), tuple(row.get("selected_source_ids") or ()))
        case_id = index.get(key)
        if case_id is None:
            raise ContractError(f"Prediction {key[0]!r} matches no supplied gold label")
        if case_id in predictions:
            raise ContractError(f"Duplicate prediction for {case_id}")
        prediction = row.get("prediction")
        if not isinstance(prediction, dict) or prediction.get("ok") is False:
            raise ContractError(f"Prediction for {case_id} is not a successful response")
        if prediction.get("status") not in STATUSES:
            raise ContractError(f"Prediction for {case_id} has an unknown status")
        if prediction.get("question") != gold[case_id]["question"]:
            raise ContractError(f"Prediction for {case_id} answers a different question")
        text(prediction.get("answer"), f"{case_id} answer")
        objects(prediction.get("citations"), f"{case_id} citations")
        predictions[case_id] = row
    return predictions


def _sentences(text_value):
    """Split at sentence ends; skip abbreviations and periods followed by lowercase."""
    pieces, start = [], 0
    for match in re.finditer(r"[。！？]|[.!?](?=\s|$)", text_value):
        end = match.end()
        if match[0] == ".":
            word = re.search(r"([\w.]+)\.$", text_value[start:end])
            following = text_value[end:].lstrip()[:1]
            if (word and word[1].lower() in ABBREVIATIONS) or following.islower():
                continue
        pieces.append(text_value[start:end])
        start = end
    pieces.append(text_value[start:])
    return pieces


# Paragraph breaks and Markdown list items end a claim even without punctuation.
BLOCK_BREAK_RE = re.compile(r"\n\s*\n|\n(?=[ \t]*(?:[-*•・]|\d+[.)])\s)")
LIST_MARKER_RE = re.compile(r"^\s*(?:[-*•・]|\d+[.)])\s+")


def answer_claims(answer):
    """Split an answer into sentence claims with the inline citation IDs each carries.

    Each paragraph and list item is split separately, so citations never cross
    from one item to the next.
    """
    claims = []
    for block in BLOCK_BREAK_RE.split(answer):
        block = LIST_MARKER_RE.sub("", block)
        moved = TRAILING_MARKERS_RE.sub(lambda m: m[2] + m[1], block)
        block_claims = []
        for sentence in _sentences(moved):
            ids = [int(value) for value in MARKER_RE.findall(sentence)]
            body = " ".join(MARKER_RE.sub(" ", sentence).split())
            body = re.sub(r"\s+([.,;:!?。、！？])", r"\1", body)
            if not re.search(r"\w", body):
                if ids and block_claims:
                    block_claims[-1]["citation_ids"] = sorted(
                        {*block_claims[-1]["citation_ids"], *ids}
                    )
                continue
            block_claims.append({"text": body, "citation_ids": sorted(set(ids))})
        claims.extend(block_claims)
    return [{"id": f"C{number}", **claim} for number, claim in enumerate(claims, 1)]


def citation_index(prediction):
    index = {}
    for row in prediction["citations"]:
        citation_id = row.get("citation_id")
        excerpt = row.get("text") or row.get("snippet")
        if type(citation_id) is int and isinstance(excerpt, str) and excerpt.strip():
            index[citation_id] = {
                "citation_id": citation_id,
                "source_id": row.get("video_id") or row.get("source_id"),
                "source_title": row.get("video_title"),
                "timestamp": row.get("timestamp_range_label"),
                "text": excerpt,
            }
    return index


def judge_payload(case, prediction):
    """Build the judge input; claims keep only the excerpts they cite and resolve."""
    claims = answer_claims(prediction["answer"])
    evidence = citation_index(prediction)
    return {
        "question": case["question"],
        "answer": prediction["answer"],
        "facts": [
            {"id": f"F{number}", "text": fact}
            for number, fact in enumerate(case["required_facts"], 1)
        ],
        "claims": [
            {
                "id": claim["id"],
                "text": claim["text"],
                "evidence": [evidence[i] for i in claim["citation_ids"] if i in evidence],
            }
            for claim in claims
        ],
    }


def _validate_decisions(decoded, payload):
    if not isinstance(decoded, dict):
        raise ContractError("Answer judge must return a JSON object")
    facts = by_key(objects(decoded.get("facts"), "fact decisions"), "id", "fact")
    claims = by_key(objects(decoded.get("claims"), "claim decisions"), "id", "claim")
    if facts.keys() != {row["id"] for row in payload["facts"]}:
        raise ContractError("Answer judge must decide every fact exactly once")
    if claims.keys() != {row["id"] for row in payload["claims"]}:
        raise ContractError("Answer judge must decide every claim exactly once")
    for row in facts.values():
        if type(row.get("conveyed")) is not bool:
            raise ContractError("Each fact decision requires a boolean conveyed")
    cited = {
        row["id"]: {item["citation_id"] for item in row["evidence"]}
        for row in payload["claims"]
    }
    for claim_id, row in claims.items():
        decision, support = row.get("decision"), row.get("supporting_citations")
        if decision not in CLAIM_LABELS:
            raise ContractError("Invalid claim decision")
        if not isinstance(support, list) or any(type(i) is not int for i in support):
            raise ContractError("supporting_citations must be a list of integers")
        if not set(support) <= cited[claim_id]:
            raise ContractError("A claim can only be supported by excerpts it cites")
        if (decision in ("supported", "partial")) != bool(support):
            raise ContractError(
                "Supported and partial claims need supporting citations; others need none"
            )
    return (
        {key: facts[key]["conveyed"] for key in sorted(facts)},
        {
            key: {
                "decision": claims[key]["decision"],
                "supporting_citations": sorted(set(claims[key]["supporting_citations"])),
            }
            for key in sorted(claims)
        },
    )


def judge_case(case, row, llm):
    """Ask an injected judge for fact and claim decisions on one saved answer."""
    prediction = row["prediction"]
    payload = judge_payload(case, prediction)
    message = json.dumps(payload, ensure_ascii=False)
    for attempt in range(MAX_JUDGE_ATTEMPTS):
        response = llm(system_prompt=DATA_RULE + JUDGE_TASK, user_message=message)
        try:
            facts, claims = _validate_decisions(
                decode_judge_reply(response["text"]), payload
            )
            break
        except (ContractError, ValueError, KeyError, TypeError) as exc:
            if attempt + 1 == MAX_JUDGE_ATTEMPTS:
                raise ContractError(f"Answer judge failed for {case['case_id']}") from exc
            message = (
                json.dumps(payload, ensure_ascii=False)
                + f"\nYour previous reply was invalid ({exc}). Follow the JSON contract exactly."
            )
    return {
        "schema": "answer-quality-judgment/v1",
        "case_id": case["case_id"],
        "prompt_version": PROMPT_VERSION,
        "judge": {key: response.get(key) for key in ("provider", "model", "cli_version")},
        "prediction_sha256": record_sha256(row),
        "gold_sha256": record_sha256(case),
        "payload": payload,
        "facts": facts,
        "claims": claims,
    }


def load_judgments(path, gold, predictions):
    judgments = {}
    for row in read_jsonl(path):
        case_id = row.get("case_id")
        if case_id not in predictions or case_id in judgments:
            raise ContractError(f"Judgment {case_id!r} matches no single prediction")
        if (
            row.get("prediction_sha256") != record_sha256(predictions[case_id])
            or row.get("gold_sha256") != record_sha256(gold[case_id])
            or row.get("prompt_version") != PROMPT_VERSION
        ):
            raise ContractError(f"Judgment for {case_id} is stale")
        payload = judge_payload(gold[case_id], predictions[case_id]["prediction"])
        if row.get("payload") != payload:
            raise ContractError(f"Judgment payload for {case_id} does not match")
        facts, claims = _validate_decisions(
            {
                "facts": [{"id": k, "conveyed": v} for k, v in row["facts"].items()],
                "claims": [{"id": k, **v} for k, v in row["claims"].items()],
            },
            payload,
        )
        judgments[case_id] = {**row, "facts": facts, "claims": claims}
    return judgments


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def score_case(case, row, judgment=None):
    """Return per-case counts; judged counts are None without a judgment."""
    prediction = row["prediction"]
    claims = answer_claims(prediction["answer"])
    resolved = citation_index(prediction)
    markers = [i for claim in claims for i in claim["citation_ids"]]
    counts = {
        "case_id": case["case_id"],
        "language": case["language"],
        "status": prediction["status"],
        "answerable": case["answerable"],
        "claims": len(claims),
        "cited_claims": sum(bool(claim["citation_ids"]) for claim in claims),
        "markers": len(markers),
        "resolved_markers": sum(i in resolved for i in markers),
        "facts": len(case["required_facts"]),
    }
    if judgment is None:
        return counts
    decisions = judgment["claims"]
    graded = [d for d in decisions.values() if d["decision"] != "no_claim"]
    cited_pairs = sum(
        len([i for i in claim["citation_ids"] if i in resolved])
        for claim in claims
        if decisions[claim["id"]]["decision"] != "no_claim"
    )
    return {
        **counts,
        "facts_conveyed": sum(judgment["facts"].values()),
        "graded_claims": len(graded),
        "supported_claims": sum(d["decision"] == "supported" for d in graded),
        "partial_claims": sum(d["decision"] == "partial" for d in graded),
        "unsupported_claims": sum(d["decision"] == "unsupported" for d in graded),
        "uncited_claims": sum(
            decisions[claim["id"]]["decision"] != "no_claim"
            and not any(i in resolved for i in claim["citation_ids"])
            for claim in claims
        ),
        "cited_pairs": cited_pairs,
        "supporting_pairs": sum(len(d["supporting_citations"]) for d in graded),
    }


def aggregate(rows):
    """Pool per-case counts into answer-quality rates."""
    total = lambda key: sum(row.get(key) or 0 for row in rows)  # noqa: E731
    answerable = [row for row in rows if row["answerable"]]
    unanswerable = [row for row in rows if not row["answerable"]]
    judged = all("facts_conveyed" in row for row in rows) and bool(rows)
    metrics = {
        "cases": len(rows),
        "answered_rate": _rate(
            sum(row["status"] == "answered" for row in rows), len(rows)
        ),
        "false_abstention_rate": _rate(
            sum(row["status"] != "answered" for row in answerable), len(answerable)
        ),
        "correct_abstention_rate": _rate(
            sum(row["status"] != "answered" for row in unanswerable), len(unanswerable)
        ),
        "marker_resolution_rate": _rate(total("resolved_markers"), total("markers")),
        "cited_claim_rate": _rate(total("cited_claims"), total("claims")),
    }
    if judged:
        metrics.update({
            "required_fact_recall": _rate(
                sum(row["facts_conveyed"] for row in answerable),
                sum(row["facts"] for row in answerable),
            ),
            "claim_support_rate": _rate(total("supported_claims"), total("graded_claims")),
            "claim_partial_rate": _rate(total("partial_claims"), total("graded_claims")),
            "claim_unsupported_rate": _rate(
                total("unsupported_claims"), total("graded_claims")
            ),
            "claim_uncited_rate": _rate(total("uncited_claims"), total("graded_claims")),
            "citation_precision": _rate(total("supporting_pairs"), total("cited_pairs")),
        })
    else:
        metrics.update(dict.fromkeys((
            "required_fact_recall", "claim_support_rate", "claim_partial_rate",
            "claim_unsupported_rate", "claim_uncited_rate", "citation_precision",
        )))
    return metrics


def score(gold, predictions, judgments=None):
    if judgments is not None and judgments.keys() != predictions.keys():
        raise ContractError("Judgments must cover every prediction")
    rows = [
        score_case(gold[case_id], row, None if judgments is None else judgments[case_id])
        for case_id, row in sorted(predictions.items())
    ]
    groups = {"all": rows}
    for status in STATUSES:
        groups[status] = [row for row in rows if row["status"] == status]
    for language in sorted({row["language"] for row in rows}):
        groups[f"language:{language}"] = [r for r in rows if r["language"] == language]
    return {
        "schema": "answer-quality-report/v1",
        "prompt_version": PROMPT_VERSION if judgments is not None else None,
        "judge": None if not judgments else next(iter(judgments.values()))["judge"],
        "metrics": {name: aggregate(group) for name, group in groups.items() if group},
        "cases": rows,
    }


def audit_sample(judgments):
    """Draw a reproducible sample of individual judge decisions for owner review."""
    pool = []
    for case_id, row in sorted(judgments.items()):
        payload = row["payload"]
        facts = {item["id"]: item["text"] for item in payload["facts"]}
        claims = {item["id"]: item for item in payload["claims"]}
        for fact_id, conveyed in row["facts"].items():
            pool.append({
                "case_id": case_id, "kind": "fact", "item_id": fact_id,
                "question": payload["question"], "answer": payload["answer"],
                "fact": facts[fact_id],
                "decision": conveyed,
            })
        for claim_id, decision in row["claims"].items():
            pool.append({
                "case_id": case_id, "kind": "claim", "item_id": claim_id,
                "question": payload["question"], "claim": claims[claim_id]["text"],
                "evidence": claims[claim_id]["evidence"], **decision,
            })
    sample = random.Random(AUDIT_SEED).sample(pool, min(AUDIT_SIZE, len(pool)))
    return [{"schema": "answer-quality-audit/v1", **item} for item in sample]
