"""Migrate chunk-indexed retrieval gold evidence to timestamp spans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from evals.evidence_spans import chunk_rows_to_spans


def main(argv: list[str] | None = None) -> int:
    """Write a migrated JSONL dataset, including when the output is the input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path, help="Input dataset JSONL")
    parser.add_argument("--corpus", required=True, type=Path, help="Source corpus JSON")
    parser.add_argument("--out", required=True, type=Path, help="Output dataset JSONL")
    args = parser.parse_args(argv)

    try:
        with args.dataset.open(encoding="utf-8") as source:
            rows = [json.loads(line) for line in source if line.strip()]
        corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
        rows = chunk_rows_to_spans(rows, corpus)
    except (OSError, KeyError, ValueError) as exc:
        parser.error(str(exc))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.out.parent, delete=False
        ) as output:
            temp_path = Path(output.name)
            for row in rows:
                json.dump(row, output, ensure_ascii=False, separators=(",", ":"))
                output.write("\n")
        temp_path.replace(args.out)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
