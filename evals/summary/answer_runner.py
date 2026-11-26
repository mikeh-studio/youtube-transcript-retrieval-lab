"""Judge and report answer quality for saved cross-source Ask predictions."""

import argparse
from pathlib import Path

from evals.contracts import ContractError, file_sha256, require_private
from evals.summary.answer_quality import (
    AUDIT_SEED,
    audit_sample,
    judge_case,
    load_ask_predictions,
    load_judgments,
    load_qa_gold,
    score,
)
from evals.summary.predictions import encode_rows, record_sha256
from evals.summary.runner import JUDGES, _output_lock, _service
from local_preview.codex_runner import CodexRunnerError
from local_preview.local_files import json_bytes, write_atomic


METRIC_ORDER = (
    "cases", "answered_rate", "false_abstention_rate", "correct_abstention_rate",
    "required_fact_recall", "claim_support_rate", "claim_partial_rate",
    "claim_unsupported_rate", "claim_uncited_rate", "citation_precision", "marker_resolution_rate",
    "cited_claim_rate",
)


def _render(result):
    lines = ["# Answer quality", ""]
    if result["judge"]:
        judge = result["judge"]
        lines.append(
            f"Judge `{judge.get('provider')}:{judge.get('model')}`, prompt "
            f"`{result['prompt_version']}`. Publish judged rates only after the owner "
            "agrees with at least 0.85 of the audit sample."
        )
    else:
        lines.append("No judgments supplied; judged rates are unavailable.")
    for group, metrics in result["metrics"].items():
        lines += ["", f"## {group}", ""]
        for name in METRIC_ORDER:
            value = metrics[name]
            if value is None:
                rendered = "unavailable"
            elif name == "cases":
                rendered = str(value)
            else:
                rendered = f"{value:.4f}"
            lines.append(f"- `{name}` {rendered}")
    return "\n".join(lines) + "\n"


def _write_report(out, result, sample):
    paths = {
        out / "results.json": json_bytes(result),
        out / "summary.md": _render(result).encode("utf-8"),
    }
    if sample is not None:
        paths[out / "judge_audit_sample.jsonl"] = encode_rows(sample)
    with _output_lock(out):
        for path in paths:
            require_private(path)
            if path.exists() or path.is_symlink():
                raise ContractError(f"Refusing to overwrite {path.name}")
        for path, content in paths.items():
            write_atomic(path, content)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("judge", "report"):
        command = commands.add_parser(name)
        command.add_argument(
            "--gold", type=Path, nargs="+", required=True,
            help="Finalized multi_source_qa JSONL files with sibling manifests",
        )
        command.add_argument("--predictions", type=Path, required=True)
        command.add_argument(
            "--out", type=Path, required=True,
            help="Output JSONL file" if name == "judge" else "Output directory",
        )
        if name == "judge":
            command.add_argument("--judge", choices=JUDGES[1:], required=True)
            command.add_argument("--model")
        else:
            command.add_argument("--judgments", type=Path)
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        gold = load_qa_gold(args.gold)
        predictions = load_ask_predictions(args.predictions, gold)
        if args.command == "judge":
            require_private(args.out)
            if args.out.exists() or args.out.is_symlink():
                raise ContractError("Refusing to overwrite existing JSONL output")
            service = _service({})

            def llm(**prompts):
                return service._llm_text_response(
                    **prompts, provider=args.judge, model=args.model,
                    max_tokens=2200, temperature=0,
                )

            rows = [
                judge_case(gold[case_id], predictions[case_id], llm)
                for case_id in sorted(predictions)
            ]
            with _output_lock(args.out.parent):
                if args.out.exists() or args.out.is_symlink():
                    raise ContractError("Refusing to overwrite existing JSONL output")
                write_atomic(args.out, encode_rows(rows))
            return
        judgments = (
            load_judgments(args.judgments, gold, predictions) if args.judgments else None
        )
        result = score(gold, predictions, judgments)
        result["inputs"] = {
            "gold_sha256": {path.name: file_sha256(path) for path in args.gold},
            "predictions_sha256": file_sha256(args.predictions),
            "judgments_sha256": file_sha256(args.judgments) if args.judgments else None,
        }
        sample = audit_sample(judgments) if judgments else None
        if sample is not None:
            result["audit_sample"] = {
                "count": len(sample), "seed": AUDIT_SEED, "sha256": record_sha256(sample),
            }
        _write_report(args.out, result, sample)
    except (ValueError, OSError, CodexRunnerError) as exc:
        parser.exit(2, f"answer quality evaluation: {exc}\n")


if __name__ == "__main__":
    main()
