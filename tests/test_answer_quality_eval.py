"""Check answer-quality claim splitting, judge validation, scoring and the runner."""

import json
from hashlib import sha256

import pytest

from evals.contracts import ContractError
from evals.summary import answer_runner
from evals.summary.answer_quality import (
    answer_claims,
    audit_sample,
    judge_case,
    load_ask_predictions,
    load_judgments,
    load_qa_gold,
    score,
)


def _citation(citation_id, source_id, excerpt):
    return {
        "citation_id": citation_id, "video_id": source_id, "video_title": source_id,
        "start_seconds": 10, "end_seconds": 20, "timestamp_range_label": "0:10-0:20",
        "text": excerpt,
    }


GOLD_ROWS = [
    {
        "id": "item_1", "review_decision": "approved", "task": "multi_source_qa",
        "language": "en", "selected_source_ids": ["demo-cork", "demo-clay"],
        "question": "How do the cork and clay lids keep the jar dry?",
        "required_facts": ["Cork absorbs moisture.", "Clay has a groove."],
        "gold_spans": [{"source_id": "demo-cork", "start": 10, "end": 20}],
    },
    {
        "id": "item_2", "review_decision": "approved", "task": "multi_source_qa",
        "language": "ja", "selected_source_ids": ["demo-cork", "demo-clay"],
        "question": "蝋の飾りはどこに付けますか？", "required_facts": ["取っ手の横に付ける。"],
        "gold_spans": [{"source_id": "demo-wax", "start": 50, "end": 60}],
    },
    {
        "id": "item_3", "review_decision": "approved", "task": "multi_source_qa",
        "language": "en", "selected_source_ids": ["demo-cork", "demo-clay"],
        "question": "What colour is the jar's lid in the third demo?",
        "required_facts": [], "answerable": False, "gold_spans": [],
    },
    {
        "id": "item_4", "review_decision": "rejected", "task": "multi_source_qa",
        "language": "en", "selected_source_ids": ["demo-cork"], "question": "Rejected?",
        "required_facts": ["x"], "gold_spans": [],
    },
]

PREDICTIONS = {
    "item_1": {
        "status": "answered",
        "answer": "The cork absorbs moisture near the rim. [1] The clay lid is blue "
                  "and has a groove [2][9].",
        "citations": [
            _citation(1, "demo-cork", "The cork at the rim drinks in stray drops."),
            _citation(2, "demo-clay", "Drops go out through a groove under the clay lid."),
        ],
    },
    "item_2": {
        "status": "insufficient_evidence",
        "answer": "抜粋では確認できません。[1]",
        "citations": [_citation(1, "demo-cork", "The cork at the rim.")],
    },
    "item_3": {
        "status": "insufficient_evidence",
        "answer": "The evidence does not say.",
        "citations": [],
    },
}

REPLIES = {
    "item_1": {
        "facts": [{"id": "F1", "conveyed": True}, {"id": "F2", "conveyed": True}],
        "claims": [
            {"id": "C1", "decision": "supported", "supporting_citations": [1]},
            {"id": "C2", "decision": "partial", "supporting_citations": [2]},
        ],
    },
    "item_2": {
        "facts": [{"id": "F1", "conveyed": False}],
        "claims": [{"id": "C1", "decision": "no_claim", "supporting_citations": []}],
    },
    "item_3": {
        "facts": [],
        "claims": [{"id": "C1", "decision": "no_claim", "supporting_citations": []}],
    },
}


@pytest.fixture
def inputs(tmp_path):
    gold_path = tmp_path / "dataset_demo.jsonl"
    gold_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in GOLD_ROWS),
        encoding="utf-8",
    )
    digest = sha256(gold_path.read_bytes()).hexdigest()
    (tmp_path / "dataset_demo.manifest.json").write_text(json.dumps({
        "dataset_id": "dataset_demo", "task": "multi_source_qa", "sha256": digest,
    }))
    predictions_path = tmp_path / "predictions.jsonl"
    predictions_path.write_text("".join(
        json.dumps({
            "label_id": row["id"], "selected_source_ids": row["selected_source_ids"],
            "prediction": {"ok": True, "question": row["question"], **PREDICTIONS[row["id"]]},
        }, ensure_ascii=False) + "\n"
        for row in GOLD_ROWS[:3]
    ), encoding="utf-8")
    return tmp_path, gold_path, predictions_path


def _judge_from(replies):
    def llm(*, system_prompt, user_message):
        payload = json.loads(user_message.split("\n")[0])
        item = next(key for key, row in PREDICTIONS.items()
                    if row["answer"] == payload["answer"])
        reply = replies[item]
        return {"text": reply if isinstance(reply, str) else json.dumps(reply),
                "provider": "fake", "model": "judge"}
    return llm


def test_claims_keep_trailing_markers_and_split_japanese():
    assert answer_claims("A cork. [1] Clay [2], then wax [3]. 確認できません。[4]") == [
        {"id": "C1", "text": "A cork.", "citation_ids": [1]},
        {"id": "C2", "text": "Clay, then wax.", "citation_ids": [2, 3]},
        {"id": "C3", "text": "確認できません。", "citation_ids": [4]},
    ]
    assert answer_claims("Jaren Jackson Jr. and Lauri Markkanen [3]. Mr. Smith led.") == [
        {"id": "C1", "text": "Jaren Jackson Jr. and Lauri Markkanen.", "citation_ids": [3]},
        {"id": "C2", "text": "Mr. Smith led.", "citation_ids": []},
    ]
    assert answer_claims("Utah won 132–107 with 3.5 assists.") == [
        {"id": "C1", "text": "Utah won 132–107 with 3.5 assists.", "citation_ids": []},
    ]


def test_gold_loading_checks_manifest_and_skips_rejected(inputs):
    tmp_path, gold_path, _ = inputs
    gold = load_qa_gold([gold_path])
    assert sorted(gold) == ["dataset_demo/item_1", "dataset_demo/item_2", "dataset_demo/item_3"]
    assert gold["dataset_demo/item_3"]["answerable"] is False
    gold_path.write_text(gold_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="manifest digest"):
        load_qa_gold([gold_path])


def test_judge_scores_claims_citations_facts_and_abstention(inputs):
    _, gold_path, predictions_path = inputs
    gold = load_qa_gold([gold_path])
    predictions = load_ask_predictions(predictions_path, gold)
    llm = _judge_from(REPLIES)
    judgments = {
        case_id: judge_case(gold[case_id], predictions[case_id], llm)
        for case_id in predictions
    }
    claim_one = judgments["dataset_demo/item_1"]["payload"]["claims"][1]
    assert [row["citation_id"] for row in claim_one["evidence"]] == [2]

    metrics = score(gold, predictions, judgments)["metrics"]["all"]
    assert metrics["answered_rate"] == pytest.approx(1 / 3)
    assert metrics["false_abstention_rate"] == pytest.approx(1 / 2)
    assert metrics["correct_abstention_rate"] == 1.0
    assert metrics["required_fact_recall"] == pytest.approx(2 / 3)
    assert metrics["claim_support_rate"] == 0.5
    assert metrics["claim_partial_rate"] == 0.5
    assert metrics["claim_uncited_rate"] == 0.0
    assert metrics["citation_precision"] == 1.0
    assert metrics["marker_resolution_rate"] == pytest.approx(3 / 4)
    assert metrics["cited_claim_rate"] == pytest.approx(3 / 4)

    unjudged = score(gold, predictions)["metrics"]["all"]
    assert unjudged["claim_support_rate"] is None
    assert unjudged["false_abstention_rate"] == pytest.approx(1 / 2)

    sample = audit_sample(judgments)
    assert len(sample) == 7
    assert {row["kind"] for row in sample} == {"fact", "claim"}


@pytest.mark.parametrize("bad_claim", [
    {"id": "C1", "decision": "supported", "supporting_citations": []},
    {"id": "C1", "decision": "unsupported", "supporting_citations": [1]},
    {"id": "C1", "decision": "supported", "supporting_citations": [2]},
    {"id": "C1", "decision": "maybe", "supporting_citations": []},
])
def test_judge_rejects_inconsistent_decisions(inputs, bad_claim):
    _, gold_path, predictions_path = inputs
    gold = load_qa_gold([gold_path])
    predictions = load_ask_predictions(predictions_path, gold)
    replies = {**REPLIES, "item_1": {**REPLIES["item_1"],
                                     "claims": [bad_claim, REPLIES["item_1"]["claims"][1]]}}
    with pytest.raises(ContractError, match="Answer judge failed"):
        judge_case(gold["dataset_demo/item_1"], predictions["dataset_demo/item_1"],
                   _judge_from(replies))


def test_judge_retries_once_after_invalid_reply(inputs):
    _, gold_path, predictions_path = inputs
    gold = load_qa_gold([gold_path])
    predictions = load_ask_predictions(predictions_path, gold)
    replies = iter(["not json", json.dumps(REPLIES["item_2"])])

    def llm(**_):
        return {"text": next(replies), "provider": "fake", "model": "judge"}

    row = judge_case(gold["dataset_demo/item_2"], predictions["dataset_demo/item_2"], llm)
    assert row["claims"]["C1"]["decision"] == "no_claim"


def test_predictions_must_match_gold_question(inputs):
    _, gold_path, predictions_path = inputs
    gold = load_qa_gold([gold_path])
    text = predictions_path.read_text(encoding="utf-8").replace(
        "How do the cork", "Why do the cork")
    predictions_path.write_text(text, encoding="utf-8")
    with pytest.raises(ContractError, match="different question"):
        load_ask_predictions(predictions_path, gold)


def test_runner_judges_then_reports_and_rejects_stale_judgments(inputs, monkeypatch):
    tmp_path, gold_path, predictions_path = inputs

    class Service:
        def _llm_text_response(self, *, system_prompt, user_message, **_):
            return _judge_from(REPLIES)(system_prompt=system_prompt, user_message=user_message)

    monkeypatch.setattr(answer_runner, "_service", lambda sources: Service())
    judgments_path = tmp_path / "judgments.jsonl"
    common = ["--gold", str(gold_path), "--predictions", str(predictions_path)]
    answer_runner.main(["judge", *common, "--out", str(judgments_path),
                        "--judge", "claude_cli"])
    out = tmp_path / "report"
    answer_runner.main(["report", *common, "--judgments", str(judgments_path),
                        "--out", str(out)])
    result = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert result["metrics"]["all"]["claim_support_rate"] == 0.5
    assert result["audit_sample"]["count"] == 7
    assert "Publish judged rates only" in (out / "summary.md").read_text(encoding="utf-8")

    gold = load_qa_gold([gold_path])
    rows = [json.loads(line) for line in judgments_path.read_text().splitlines()]
    rows[0]["claims"]["C1"]["decision"] = "unsupported"
    rows[0]["claims"]["C1"]["supporting_citations"] = []
    rows[0]["prediction_sha256"] = "0" * 64
    judgments_path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(ContractError, match="stale"):
        load_judgments(judgments_path, gold, load_ask_predictions(predictions_path, gold))


def test_list_items_keep_their_own_citations():
    claims = answer_claims("- Curry hit nine threes [1]\n- The Jazz lost by twelve [2]")
    assert [(c["text"], c["citation_ids"]) for c in claims] == [
        ("Curry hit nine threes", [1]),
        ("The Jazz lost by twelve", [2]),
    ]
    numbered = answer_claims("1. First point [1]\n2. Second point [2]\n\nA closing note [3]")
    assert [c["citation_ids"] for c in numbered] == [[1], [2], [3]]
