"""Generate, judge, score and report summary evaluations from local files."""

import argparse
import fcntl
import os
import random
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from evals.contracts import ROOT, ContractError, file_sha256, require_private
from evals.summary.dataset import load_gold, load_sources
from evals.summary.judge import judge, load_judgments
from evals.summary.predictions import (
    encode_rows,
    generate,
    load_predictions,
    record_sha256,
)
from evals.summary.scoring import score
from local_preview.codex_runner import CodexRunnerError
from local_preview.local_files import json_bytes, write_atomic


PROVIDERS = ("codex_cli", "claude_cli", "chatgpt", "claude", "sakana")
JUDGES = ("none", "claude_cli", "codex_cli", "claude", "chatgpt")
AUDIT_SEED = 42
AUDIT_SIZE = 20


def audit_sample(gold, predictions, judgments):
    """Sample individual decisions and attach the inputs needed for review."""
    if judgments is None:
        return []
    pool = [
        row
        for _, kinds in sorted(judgments.items())
        for _, decisions in sorted(kinds.items())
        for _, row in sorted(decisions.items())
    ]
    sample = random.Random(AUDIT_SEED).sample(pool, min(AUDIT_SIZE, len(pool)))
    items = {
        (system, source_id, item["id"]): item
        for (system, source_id), prediction in predictions.items()
        for item in prediction["items"]
    }
    cases = []
    for row in sample:
        source = gold[row["source_id"]]
        item = items[row["system"], row["source_id"], row["item_id"]]
        context = (
            {
                "gold_points": [
                    {"id": point["id"], "text": point["text"]}
                    for point in source["key_points"]
                ]
            }
            if row["kind"] == "match"
            else {"window": row.get("window", [])}
        )
        cases.append(
            {
                **row,
                **context,
                "schema": "summary-judge-audit/v1",
                "source_fingerprint": source["source_fingerprint"],
                "language": source["language"],
                "start": item["start"],
                "end": item["end"],
                "prediction_text": item["text"],
            }
        )
    return cases


def _service(sources):
    # Suppress HTTP server bootstrap; these commands only need stored transcripts.
    flag = "YT_RAG_SKIP_GLOBAL_SERVICE"
    previous = os.environ.get(flag)
    os.environ[flag] = "1"
    try:
        from local_preview.local_api import LocalRAGService, RAGEngine
    finally:
        if previous is None:
            os.environ.pop(flag, None)
        else:
            os.environ[flag] = previous

    return LocalRAGService(engine=RAGEngine(library=SimpleNamespace(videos=sources)))


@contextmanager
def _output_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write_run(out, result, sample):
    results = out / "results.json"
    require_private(results)
    with _output_lock(out):
        if results.exists() or results.is_symlink():
            raise ContractError("Refusing to overwrite existing results.json")
        paths = {results: json_bytes(result)}
        if sample is not None:
            lines = [
                "# Summary evaluation",
                "",
                f"Timestamp pairing uses `{result['timestamp_pairing']}`.",
            ]
            for system, entry in result["systems"].items():
                name = " ".join(system.split())
                fence = "`" * (1 + max(map(len, re.findall(r"`+", name)), default=0))
                lines += ["", f"## {fence} {name} {fence}", ""]
                for metric, value in entry["aggregate"]["metrics"].items():
                    rendered = "unavailable" if value is None else f"{value:.4f}"
                    lines.append(f"- `{metric}` {rendered}")
            if result["timestamp_pairing"] == "judge":
                lines += [
                    "",
                    f"The audit sample contains {len(sample)} decisions. Publish judged numbers only "
                    "after owner agreement reaches 0.85.",
                ]
            paths = {
                out / "summary.md": ("\n".join(lines) + "\n").encode("utf-8"),
                out / "judge_audit_sample.jsonl": encode_rows(sample),
                **paths,
            }
        for path in paths:
            require_private(path)
            if path.is_symlink():
                raise ContractError(f"Output file is a symlink: {path.name}")
        for path, content in paths.items():
            write_atomic(path, content)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "score", "judge", "report"):
        command = commands.add_parser(name)
        command.add_argument("--gold", type=Path, required=True)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument(
            "--out",
            type=Path,
            required=True,
            help="Output JSONL file"
            if name in ("generate", "judge")
            else "Output directory",
        )
        if name in ("generate", "judge"):
            command.add_argument("--model")
            command.add_argument(
                "--transcripts", type=Path, help="Optional stored transcript JSON"
            )
            command.add_argument("--data-dir", type=Path, default=ROOT / "data")
        if name == "generate":
            command.add_argument("--provider", choices=PROVIDERS, required=True)
            command.add_argument("--system")
        else:
            command.add_argument("--predictions", type=Path, required=True)
        if name == "judge":
            command.add_argument("--judge", choices=JUDGES, default="none")
        if name in ("score", "report"):
            command.add_argument("--judgments", type=Path)
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "judge" and args.judge == "none":
        parser.error("judge requires an explicit --judge provider")
    try:
        gold, gold_hash = load_gold(args.gold, args.manifest)
        if args.command in ("generate", "judge"):
            require_private(args.out)
            if args.out.exists() or args.out.is_symlink():
                raise ContractError("Refusing to overwrite existing JSONL output")
            if args.command == "judge":
                predictions = load_predictions(args.predictions, gold)
            sources = load_sources(
                gold, data_dir=args.data_dir, transcripts=args.transcripts
            )
            service = _service(sources)
            if args.command == "generate":
                rows = generate(
                    gold,
                    service.summarize_video_transcript,
                    provider=args.provider,
                    model=args.model,
                    system=args.system,
                )
            else:

                def llm(**prompts):
                    return service._llm_text_response(
                        **prompts,
                        provider=args.judge,
                        model=args.model,
                        max_tokens=2200,
                        temperature=0,
                    )

                rows = judge(gold, predictions, sources, llm)
            with _output_lock(args.out.parent):
                if args.out.exists() or args.out.is_symlink():
                    raise ContractError("Refusing to overwrite existing JSONL output")
                write_atomic(args.out, encode_rows(rows))
        else:
            predictions = load_predictions(args.predictions, gold)
            judgments = (
                load_judgments(args.judgments, gold, predictions)
                if args.judgments
                else None
            )
            result = score(gold, predictions, judgments)
            result["inputs"] = {
                "gold_sha256": gold_hash,
                "manifest_sha256": file_sha256(args.manifest),
                "predictions_sha256": file_sha256(args.predictions),
                "judgments_sha256": file_sha256(args.judgments)
                if args.judgments
                else None,
            }
            sample = (
                audit_sample(gold, predictions, judgments)
                if args.command == "report"
                else None
            )
            if sample is not None:
                result["audit_sample"] = {
                    "count": len(sample),
                    "seed": AUDIT_SEED,
                    "sha256": record_sha256(sample),
                }
            _write_run(args.out, result, sample)
    except (ValueError, OSError, CodexRunnerError) as exc:
        parser.exit(2, f"summary evaluation: {exc}\n")


if __name__ == "__main__":
    main()
