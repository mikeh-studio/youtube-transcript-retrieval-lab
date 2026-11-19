"""Hand-checked bilingual summary metrics and offline entry points."""

import json
import stat
from types import SimpleNamespace

import pytest

from evals.contracts import ContractError, ROOT
from evals.summary import runner
from evals.summary.dataset import load_gold, load_sources, read_jsonl
from evals.summary.judge import judge, load_judgments, validate_judgments
from evals.summary.predictions import generate, load_predictions, record_sha256
from evals.summary.scoring import score


FIXTURE = ROOT / "evals" / "fixtures" / "summary_synthetic_v1"
DATASET = ROOT / "evals" / "datasets" / "summary_synthetic_v1"
GOLD = DATASET.with_suffix(".gold.jsonl")
MANIFEST = DATASET.with_suffix(".manifest.json")
PREDICTIONS = FIXTURE.with_suffix(".predictions.jsonl")
JUDGMENTS = FIXTURE.with_suffix(".judgments.jsonl")
TRANSCRIPTS = FIXTURE.with_suffix(".json")


@pytest.fixture
def inputs():
    gold, _ = load_gold(GOLD, MANIFEST)
    return gold, load_predictions(PREDICTIONS, gold)


def cli_args(command, out):
    args = [
        command,
        "--gold",
        str(GOLD),
        "--manifest",
        str(MANIFEST),
        "--out",
        str(out),
    ]
    return args if command == "generate" else [*args, "--predictions", str(PREDICTIONS)]


def test_literal_proxy_scores(inputs):
    gold, predictions = inputs
    result = score(gold, predictions)
    assert result["timestamp_pairing"] == "span_proxy"
    system = result["systems"]["synthetic"]
    assert system["aggregate"]["metrics"] == pytest.approx(
        {
            "anchor_resolution_rate": 4 / 6,
            "ts_hit@15s": 2 / 4,
            "ts_hit@30s": 3 / 4,
            "ts_mae_s": 90 / 4,
            "keypoint_span_recall@30s": 4 / 6,
            "duplicate_anchor_rate": 1 / 4,
            "language_match_rate": 6 / 7,
        }
    )
    assert (
        system["sources"]["synthetic_en"]["metrics"]["duplicate_anchor_rate"] == 1 / 2
    )
    assert system["sources"]["synthetic_ja"]["metrics"]["language_match_rate"] == 2 / 3
    assert system["sources"]["synthetic_en"]["counts"]["known_anchor_sources"] == 3
    assert system["aggregate"]["counts"]["predictions"] == 7


def test_judged_scores_use_semantic_matches_even_outside_span(inputs):
    gold, predictions = inputs
    judgments = load_judgments(JUDGMENTS, gold, predictions)
    assert {
        row["window_policy"]
        for decisions in judgments.values()
        for row in decisions["faithfulness"].values()
    } == {"anchor_only_v1"}
    result = score(gold, predictions, judgments)
    assert result["timestamp_pairing"] == "judge"
    system = result["systems"]["synthetic"]
    metrics = system["aggregate"]["metrics"]
    assert metrics == pytest.approx(
        {
            "anchor_resolution_rate": 4 / 6,
            "ts_hit@15s": 2 / 5,
            "ts_hit@30s": 3 / 5,
            "ts_mae_s": 140 / 5,
            "keypoint_span_recall@30s": 4 / 6,
            "duplicate_anchor_rate": 1 / 4,
            "language_match_rate": 6 / 7,
            "keypoint_recall_w": 11 / 12,
            "keypoint_precision": 5 / 7,
            "faithfulness_rate": 3 / 7,
            "partial_rate": 2 / 7,
            "window_policy": "anchor_only_v1",
        }
    )
    assert system["judge"]["model"] == "fake-judge"
    assert system["sources"]["synthetic_en"]["metrics"]["ts_mae_s"] == 95 / 3


def test_span_proxy_uses_each_prediction_once_but_recall_checks_all_spans(inputs):
    gold, predictions = inputs
    en = gold["synthetic_en"]
    en["key_points"][0]["spans"].append({"start": 300, "end": 315})
    prediction = predictions["synthetic", "synthetic_en"]
    prediction["items"] = [{**prediction["items"][0], "start": 330, "end": 330}]
    metrics = score(gold, predictions)["systems"]["synthetic"]["sources"][
        "synthetic_en"
    ]["metrics"]
    assert metrics["keypoint_span_recall@30s"] == 2 / 3
    assert metrics["ts_mae_s"] == 30


def test_empty_item_lists_keep_unavailable_rates(inputs):
    gold, predictions = inputs
    for row in predictions.values():
        row["items"] = []
    metrics = score(gold, predictions)["systems"]["synthetic"]["aggregate"]["metrics"]
    assert metrics["ts_mae_s"] is None
    assert metrics["anchor_resolution_rate"] is None
    assert metrics["language_match_rate"] is None
    assert metrics["keypoint_span_recall@30s"] == 0


def test_gold_hash_and_source_fingerprint_are_checked(inputs, tmp_path):
    gold, _ = inputs
    path = tmp_path / "gold.jsonl"
    path.write_bytes(GOLD.read_bytes() + b"\n")
    with pytest.raises(ContractError, match="SHA-256 mismatch"):
        load_gold(path, MANIFEST)
    transcript = json.loads(TRANSCRIPTS.read_text())
    assert [
        len(row["full_transcript"]["segments"]) for row in transcript["sources"]
    ] == [20, 20]
    transcript["sources"][0]["full_transcript"]["segments"][0]["text"] += " Drift."
    path.write_text(json.dumps(transcript))
    with pytest.raises(ContractError, match="fingerprint mismatch"):
        load_sources(gold, data_dir=None, transcripts=path)


def test_generate_preserves_legacy_cache_and_records_model_provenance(inputs):
    gold, _ = inputs
    calls = []

    def summarize(**kwargs):
        calls.append(kwargs)
        return {
            "provider": kwargs["provider"],
            "model": "fixture-model",
            "cli_version": "fixture-cli 1",
            "language": kwargs["language"],
            "generation_details": {"cache_hit": True},
            "summary": [
                {"tldr": "An invented note.", "start": 0, "end": 0},
                {
                    "title": "Garden",
                    "tldr": "The garden is green.",
                    "start": 20,
                    "end": 25,
                    "timestamp_source": "anchor_match",
                    "segment_index": 1,
                },
            ],
        }

    rows = generate(gold, summarize, provider="codex_cli")
    assert [(call["video_id"], call["language"]) for call in calls] == [
        ("synthetic_en", "en"),
        ("synthetic_ja", "ja"),
    ]
    assert rows[0]["model"] == "fixture-model" and rows[0]["cache_hit"] is True
    assert rows[0]["cli_version"] == "fixture-cli 1"
    assert rows[0]["items"][0]["timestamp_source"] is None
    assert rows[0]["items"][0]["evidence"] == []
    assert rows[0]["items"][1]["evidence"] == [{"start": 20, "end": 25}]


def fake_llm(llm_calls, reply_format="{body}"):
    fixture = {
        (row["kind"], row["item_id"]): row["decision"] for row in read_jsonl(JUDGMENTS)
    }

    def llm(*, system_prompt, user_message):
        payload = json.loads(user_message)
        llm_calls.append((system_prompt, payload))
        kind = "match" if "gold" in payload else "faithfulness"
        decisions = [
            {"item_id": item["item_id"], "decision": fixture[kind, item["item_id"]]}
            for item in payload["predictions"]
        ]
        return {
            "text": reply_format.format(body=json.dumps({"decisions": decisions})),
            "provider": "codex_cli",
            "model": "fixture-judge",
            "cli_version": "fixture-cli 1",
        }

    return llm


@pytest.mark.parametrize(
    "reply_format", ["{body}", " \n```json\n{body}\n```\n ", "```\r\n{body}\r\n```"]
)
def test_judge_sends_separate_windows_and_validates_all_decisions(inputs, reply_format):
    gold, predictions = inputs
    sources = load_sources(gold, data_dir=None, transcripts=TRANSCRIPTS)
    calls = []
    rows = judge(gold, predictions, sources, fake_llm(calls, reply_format))
    assert len(calls) == 4 and len(rows) == 14
    assert all("untrusted data" in prompt for prompt, _ in calls)
    assert all("window" not in item for item in calls[0][1]["predictions"])
    first, unmatched, _, distant_match = calls[1][1]["predictions"]
    assert [line["start"] for line in first["window"]] == [0, 20, 30, 60, 80]
    assert [line["start"] for line in unmatched["window"]] == [0, 20, 30, 60, 80]
    assert [line["start"] for line in distant_match["window"]] == [
        200, 220, 240, 260, 280, 300
    ]
    assert rows[7]["window"] == distant_match["window"]
    assert rows[0]["judge"]["cli_version"] == "fixture-cli 1"
    with pytest.raises(ContractError, match="exactly once"):
        validate_judgments(rows[1:], gold, predictions)
    rows[1]["decision"] = rows[0]["decision"]
    with pytest.raises(ContractError, match="one-to-one"):
        validate_judgments(rows, gold, predictions)
    predictions["synthetic", "synthetic_en"]["items"][0]["text"] += " Drift."
    with pytest.raises(ContractError, match="does not match"):
        validate_judgments(rows, gold, predictions)


def test_judge_caps_only_gold_ranges_and_sends_each_line_in_timeline_order(inputs):
    gold, predictions = inputs
    key = ("synthetic", "synthetic_en")
    source = gold[key[1]]
    source["duration_s"] = 1200
    point = source["key_points"][2]
    point["onset"] = 110
    point["spans"] = [
        {"start": 800, "end": 850},
        {"start": 500, "end": 750},
        {"start": 640, "end": 660},
        {"start": 100, "end": 130},
    ]
    row = predictions[key]
    item = {**row["items"][3], "start": 1000, "end": 1010}
    predictions = {key: {**row, "items": [item]}}
    segments = [
        {"start": 1010, "end": 1011, "text": "The log is still on the shelf."},
        {"start": 701, "end": 705, "text": "The shelf has three hooks."},
        {"start": 95, "end": 101, "text": "They pick up the log."},
        {"start": 400, "end": 405, "text": "The lamp is on."},
        {"start": 699, "end": 703, "text": "The shelf can be reached."},
        {"start": 645, "end": 649, "text": "They lower the shelf."},
        {"start": 810, "end": 820, "text": "They count the books."},
    ]
    sources = {key[1]: {"full_transcript": {"segments": segments}}}
    calls = []
    judge(gold, predictions, sources, fake_llm(calls))
    assert [line["start"] for line in calls[1][1]["predictions"][0]["window"]] == [
        95, 645, 699, 1010
    ]


def test_judge_still_rejects_supported_with_an_empty_window(inputs):
    gold, predictions = inputs
    sources = load_sources(gold, data_dir=None, transcripts=TRANSCRIPTS)
    unmatched = predictions["synthetic", "synthetic_en"]["items"][1]
    unmatched.update(start=500, end=510)
    with pytest.raises(
        ContractError, match="An empty evidence window requires unsupported"
    ):
        judge(gold, predictions, sources, fake_llm([]))


@pytest.mark.parametrize("policy", ["unknown", None, []])
def test_validate_judgments_rejects_unknown_window_policy(inputs, policy):
    gold, predictions = inputs
    rows = read_jsonl(JUDGMENTS)
    next(row for row in rows if row["kind"] == "faithfulness")["window_policy"] = policy
    with pytest.raises(ContractError, match="Unknown faithfulness window_policy"):
        validate_judgments(rows, gold, predictions)


def test_score_rejects_mixed_window_policies_across_systems(inputs):
    gold, predictions = inputs
    source_id = "synthetic_ja"
    other = predictions.pop(("synthetic", source_id))
    other["system"] = "another-system"
    predictions["another-system", source_id] = other
    rows = read_jsonl(JUDGMENTS)
    for row in rows:
        if row["source_id"] == source_id:
            row.update(system="another-system", prediction_sha256=record_sha256(other))
            if row["kind"] == "faithfulness":
                row["window_policy"] = "anchor_plus_matched_gold_v1"
    judgments = validate_judgments(rows, gold, predictions)
    with pytest.raises(
        ContractError, match="Cannot score mixed faithfulness window policies"
    ):
        score(gold, predictions, judgments)


@pytest.mark.parametrize(
    "reply",
    [
        'Here is the JSON.\n```json\n{"decisions": []}\n```',
        '```json\n{"decisions": []}\n```\nDone.',
        '```json\n{"decisions": []}\n```\n```\n{}\n```',
        '```json\n{"decisions": []}',
        '```json\n{"decisions": [], "decisions": []}\n```',
        '```json\n{"decisions": [], "score": NaN}\n```',
        '```json\n{"decisions": [], "score": Infinity}\n```',
    ],
)
def test_judge_rejects_invalid_json_replies(inputs, reply):
    gold, predictions = inputs
    sources = load_sources(gold, data_dir=None, transcripts=TRANSCRIPTS)
    llm = fake_llm([])

    def invalid_faithfulness_reply(**request):
        response = llm(**request)
        if "gold" not in json.loads(request["user_message"]):
            response["text"] = reply
        return response

    with pytest.raises(
        ContractError, match="^Judge faithfulness did not return valid JSON$"
    ):
        judge(gold, predictions, sources, invalid_faithfulness_reply)


def test_score_report_cli_is_offline_and_refuses_overwrite(
    monkeypatch, tmp_path, capsys
):
    def unexpected(*args):
        pytest.fail("Offline scoring must not initialize a provider")

    monkeypatch.setattr(runner, "_service", unexpected)
    runner.main(cli_args("score", tmp_path / "score"))
    score_path = tmp_path / "score" / "results.json"
    assert json.loads(score_path.read_text())["timestamp_pairing"] == "span_proxy"
    assert stat.S_IMODE(score_path.stat().st_mode) == 0o600

    out = tmp_path / "report"
    flags = ["--judgments", str(JUDGMENTS)]
    runner.main(cli_args("report", out) + flags)
    results = out / "results.json"
    assert json.loads(results.read_text())["audit_sample"]["count"] == 14
    summary = (out / "summary.md").read_text()
    assert "0.85" in summary
    assert "`window_policy` `anchor_only_v1`" in summary
    sample = out / "judge_audit_sample.jsonl"
    assert len(read_jsonl(sample)) == 14
    runner.main(cli_args("report", tmp_path / "repeat") + flags)
    assert sample.read_bytes() == (tmp_path / "repeat" / sample.name).read_bytes()
    original = results.read_bytes()
    with pytest.raises(SystemExit) as exc:
        runner.main(cli_args("report", out) + flags)
    assert (
        exc.value.code == 2
        and "overwrite existing results.json" in capsys.readouterr().err
    )
    assert results.read_bytes() == original
    with pytest.raises(SystemExit) as exc:
        runner.main(cli_args("judge", tmp_path / "judge.jsonl"))
    assert exc.value.code == 2 and "explicit --judge" in capsys.readouterr().err


def test_generate_and_explicit_judge_cli_use_injected_service(monkeypatch, tmp_path):
    calls = []
    llm = fake_llm(calls)

    def respond(*, provider, model, max_tokens, temperature, **prompts):
        assert provider == "codex_cli" and model == "fixture-judge"
        return llm(**prompts)

    def summarize(**request):
        return {
            "provider": request["provider"],
            "model": "stub",
            "language": request["language"],
            "generation_details": {"cache_hit": False},
            "summary": [{"tldr": "Clouds.", "start": 1, "end": 2}],
        }

    monkeypatch.setattr(
        runner,
        "_service",
        lambda sources: SimpleNamespace(
            summarize_video_transcript=summarize, _llm_text_response=respond
        ),
    )
    out = tmp_path / "predictions.jsonl"
    runner.main(
        cli_args("generate", out)
        + ["--provider", "claude_cli", "--transcripts", str(TRANSCRIPTS)]
    )
    assert len(read_jsonl(out)) == 2
    assert not calls
    out = tmp_path / "judgments.jsonl"
    runner.main(
        cli_args("judge", out)
        + [
            "--judge",
            "codex_cli",
            "--model",
            "fixture-judge",
            "--transcripts",
            str(TRANSCRIPTS),
        ]
    )
    rows = read_jsonl(out)
    assert len(rows) == 14 and len(calls) == 4
    for row in rows:
        if row["kind"] == "faithfulness":
            assert row["window_policy"] == "anchor_plus_matched_gold_v1"
        else:
            assert "window_policy" not in row

    report = tmp_path / "report"
    runner.main(cli_args("report", report) + ["--judgments", str(out)])
    result = json.loads((report / "results.json").read_text())
    metrics = result["systems"]["synthetic"]["aggregate"]["metrics"]
    assert metrics["window_policy"] == "anchor_plus_matched_gold_v1"
    summary = (report / "summary.md").read_text()
    assert "`window_policy` `anchor_plus_matched_gold_v1`" in summary


def test_audit_sampler_caps_at_20_and_is_seeded(inputs):
    gold, predictions = inputs
    for (_, source_id), row in list(predictions.items()):
        predictions["another-system", source_id] = {**row, "system": "another-system"}
    rows = read_jsonl(JUDGMENTS)
    for row in list(rows):
        other = predictions["another-system", row["source_id"]]
        rows.append(
            {
                **row,
                "system": "another-system",
                "prediction_sha256": record_sha256(other),
            }
        )
    pool = validate_judgments(rows, gold, predictions)
    sample = runner.audit_sample(gold, predictions, pool)
    assert (
        len(
            {
                (row["system"], row["source_id"], row["kind"], row["item_id"])
                for row in sample
            }
        )
        == 20
    )
    assert sample == runner.audit_sample(
        gold, predictions, dict(reversed(list(pool.items())))
    )
