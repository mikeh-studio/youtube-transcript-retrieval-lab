"""Check cross-source generation payloads, P1 metrics, and opt-in judging."""

import json
from pathlib import Path

import pytest

from evals.contracts import ContractError
from evals.summary.cross_source import (
    answer_source_coverage,
    answer_span_recall,
    generate_ask_prediction,
    generate_collection_prediction,
    score_ask,
    score_collection,
)


@pytest.fixture
def fixture():
    path = Path(__file__).resolve().parents[1] / "evals/fixtures/cross_source_synthetic_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_generate_calls_injected_systems_without_gold(fixture):
    case = fixture["qa"]

    def ask(**request):
        assert request == {
            "question": case["question"], "language": "en", "video_routing": "multi_vector",
            "provider": "test-provider", "model": "test-model",
        }
        return fixture["ask"]

    def collection(**request):
        assert request == {
            "source_ids": case["selected_source_ids"], "language": "en",
            "focus": case["question"], "provider": "test-provider", "model": "test-model",
        }
        return fixture["collection"]

    assert generate_ask_prediction(
        case, ask, provider="test-provider", model="test-model"
    ) is fixture["ask"]
    assert generate_collection_prediction(
        case, collection, provider="test-provider", model="test-model"
    ) is fixture["collection"]


def test_ask_scores_citations_with_p1_overlap_and_abstention(fixture):
    case, prediction = fixture["qa"], fixture["ask"]
    assert score_ask(case, prediction) == {
        "answer_source_coverage": 1.0, "answer_span_recall": 0.5,
    }
    assert score_ask(case, prediction, min_overlap_ratio=0.51)["answer_span_recall"] == 0
    assert score_ask(case, {**prediction, "status": "insufficient_evidence"}) == {
        "answer_source_coverage": 0.0, "answer_span_recall": 0.0,
    }
    assert answer_span_recall([{"source_id": "demo-cork", "start": 15, "end": 15}],
                              prediction["citations"]) == 1
    assert answer_span_recall(case["gold_spans"], [{"video_id": "off-scope",
                                                   "start": 10, "end": 40}]) == 0
    assert answer_source_coverage(case["gold_spans"], [
        {"video_id": "demo-cork", "start": -1, "end": 20},
        {"video_id": "demo-clay", "start": 30},
    ]) == 0


def _judge_decisions():
    return {
        "theme_matches": [{"id": "T1", "gold_id": "item_1"}],
        "stances": [{"id": "T1.P1", "supported": True}, {"id": "T1.P2", "supported": False}],
    }


@pytest.mark.parametrize(
    "reply_format", ["{body}", "```json\n{body}\n```", "```\n{body}\n```"]
)
def test_collection_scope_and_semantic_judge_are_independent(fixture, reply_format):
    gold = fixture["qa"]["gold_spans"]
    prediction = fixture["collection"]
    assert score_collection(prediction, gold_spans=gold) == {
        "answer_source_coverage": 1.0, "answer_span_recall": 1.0,
        "theme_recall": None, "stance_source_precision": None,
    }
    gold = gold + [{"source_id": "demo-wax", "start": 50, "end": 60}]
    prediction["themes"][0]["perspectives"][1]["citations"] = ["E1", "unknown"]
    prediction["source_ids"].remove("demo-wax")
    prediction["themes"][0]["perspectives"].append({
        "source_id": "demo-wax", "stance": "adds", "statement": "Dots go by the handle.",
        "citations": ["E3"],
    })

    def judge(**request):
        payload = json.loads(request["user_message"])
        first, second, third = payload["predicted_themes"][0]["perspectives"]
        assert first["evidence"] == [prediction["evidence_catalog"][0]["text"]]
        assert second["evidence"] == third["evidence"] == []
        decisions = _judge_decisions()
        decisions["stances"].append({"id": "T1.P3", "supported": False})
        return {"text": reply_format.format(body=json.dumps(decisions))}

    assert score_collection(prediction, gold_spans=gold, gold_themes=fixture["gold_themes"],
                            judge_llm=judge) == {
        "answer_source_coverage": 1 / 3, "answer_span_recall": 1 / 3,
        "theme_recall": 0.5, "stance_source_precision": 1 / 3,
    }


def test_collection_eval_ignores_not_returned_theme_placeholders(fixture):
    placeholder = {
        "theme_index": 1,
        "title": "Wax decorations",
        "summary": "",
        "perspectives": [],
        "status": "not_returned",
    }
    mixed = {
        **fixture["collection"],
        "themes": [placeholder, *fixture["collection"]["themes"]],
    }

    def judge(**request):
        payload = json.loads(request["user_message"])
        assert [row["id"] for row in payload["predicted_themes"]] == ["T1"]
        assert [row["title"] for row in payload["predicted_themes"]] == ["Lid moisture control"]
        return {"text": json.dumps(_judge_decisions())}

    assert score_collection(
        mixed, gold_spans=fixture["qa"]["gold_spans"],
        gold_themes=fixture["gold_themes"], judge_llm=judge,
    ) == {
        "answer_source_coverage": 1.0,
        "answer_span_recall": 1.0,
        "theme_recall": 0.5,
        "stance_source_precision": 0.5,
    }

    def unexpected_judge(**_):
        pytest.fail("Judge should not be called when every theme is not_returned")

    all_missing = {**fixture["collection"], "themes": [placeholder]}
    assert score_collection(
        all_missing, gold_spans=fixture["qa"]["gold_spans"],
        gold_themes=fixture["gold_themes"], judge_llm=unexpected_judge,
    ) == {
        "answer_source_coverage": 0.0,
        "answer_span_recall": 0.0,
        "theme_recall": 0.0,
        "stance_source_precision": None,
    }


@pytest.mark.parametrize(
    "reply_format",
    [
        "Here is the JSON.\n```json\n{body}\n```",
        "```json\n{body}\n```\nDone.",
        "```json\n{body}\n```\n```\n{{}}\n```",
        "```json\n{body}",
        '```json\n{{"theme_matches": [], "theme_matches": []}}\n```',
    ],
)
def test_collection_judge_rejects_invalid_json_replies(fixture, reply_format):
    reply = reply_format.format(body=json.dumps(_judge_decisions()))
    with pytest.raises(ContractError, match="^Cross-source judge must return valid JSON$"):
        score_collection(
            fixture["collection"], gold_themes=fixture["gold_themes"],
            judge_llm=lambda **_: {"text": reply},
        )


@pytest.mark.parametrize("field", ["stances", "theme_matches"])
def test_invalid_judgment_is_rejected(fixture, field):
    decisions = _judge_decisions()
    decisions[field] = []
    with pytest.raises(ContractError, match="must decide every"):
        score_collection(fixture["collection"], gold_themes=fixture["gold_themes"],
                         judge_llm=lambda **_: {"text": json.dumps(decisions)})
