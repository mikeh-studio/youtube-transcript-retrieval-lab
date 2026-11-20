"""Validate pinned summary gold and canonical transcript sources."""

import math
import re

from evals.contracts import (
    ContractError,
    ROOT,
    SHA256,
    file_sha256,
    read_json,
    require_private,
)
from local_preview.transcript_lines import fingerprint_lines, numbered_lines
from pipelines.export_library import parse_json


GOLD_SCHEMA = "summary-gold/v1"
LANGUAGES = {"en", "ja"}
FIXTURES = {
    ROOT / "evals" / "datasets" / f"summary_synthetic_v1.{suffix}"
    for suffix in ("gold.jsonl", "manifest.json")
} | {
    ROOT / "evals" / "fixtures" / f"summary_synthetic_v1.{suffix}"
    for suffix in ("json", "predictions.jsonl", "judgments.jsonl")
}


def require_input_path(path):
    if path.resolve() not in FIXTURES:
        require_private(path)


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be nonempty text")
    return value


def timestamp(value, name, duration=math.inf):
    if (
        type(value) not in (int, float)
        or not 0 <= value <= duration
        or value == math.inf
    ):
        raise ContractError(f"{name} must be finite and within the source timeline")
    return value


def objects(value, name, *, nonempty=False):
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(row, dict) for row in value)
    ):
        raise ContractError(f"{name} must be a valid list of objects")
    return value


def span(value, name, duration=math.inf, *, strict=False):
    start = timestamp(value.get("start"), f"{name}.start", duration)
    end = timestamp(value.get("end"), f"{name}.end", duration)
    if start > end or (strict and start == end):
        raise ContractError(f"{name} requires ordered timestamps")
    return start, end


def by_key(rows, field, name):
    keyed = {}
    for row in rows:
        key = text(row.get(field), f"{name}.{field}")
        if key in keyed:
            raise ContractError(f"Duplicate {name}.{field}")
        keyed[key] = row
    return keyed


def read_jsonl(path):
    require_input_path(path)
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = parse_json(line)
                if not isinstance(row, dict):
                    raise ValueError
            except ValueError as exc:
                raise ContractError(
                    f"{path.name} line {number} must be a JSON object"
                ) from exc
            rows.append(row)
    return rows


def load_gold(path, manifest):
    """Verify the manifest's gold SHA-256 and validate each gold row."""
    require_input_path(path)
    require_input_path(manifest)
    spec = read_json(manifest)
    expected = spec.get("sha256") if isinstance(spec, dict) else None
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise ContractError("manifest.sha256 must be a lowercase SHA-256 digest")
    if file_sha256(path) != expected:
        raise ContractError("Gold SHA-256 mismatch")
    rows = by_key(read_jsonl(path), "source_id", "gold source")
    if file_sha256(path) != expected:
        raise ContractError("Gold changed while it was being read")
    if not rows:
        raise ContractError("Gold must contain at least one source")
    for row in rows.values():
        if row.get("schema") != GOLD_SCHEMA:
            raise ContractError(f"Gold rows must declare schema {GOLD_SCHEMA}")
        if row.get("language") not in ("en", "ja"):
            raise ContractError("Gold language must be en or ja")
        if not SHA256.fullmatch(str(row.get("source_fingerprint", ""))):
            raise ContractError("Gold source_fingerprint must be a SHA-256 digest")
        duration = timestamp(row.get("duration_s"), "duration_s")
        if duration <= 0:
            raise ContractError("duration_s must be positive")
        points = objects(row.get("key_points"), "key_points", nonempty=True)
        for point in by_key(points, "id", "key point").values():
            text(point.get("text"), "key point text")
            if (
                type(point.get("importance")) is not int
                or not 1 <= point["importance"] <= 3
            ):
                raise ContractError(
                    "Key-point importance must be an integer from 1 to 3"
                )
            onset = timestamp(point.get("onset"), "key point onset", duration)
            spans = [
                span(value, "key point span", duration, strict=True)
                for value in objects(
                    point.get("spans"), "key point spans", nonempty=True
                )
            ]
            if not any(start <= onset <= end for start, end in spans):
                raise ContractError("Key point onset must be within a gold span")
        for section in objects(row.get("sections", []), "sections"):
            timestamp(section.get("start"), "section start", duration)
            text(section.get("title"), "section title")
    return rows, expected


def load_sources(gold, *, data_dir, transcripts=None):
    """Load and verify stored segments without a retrieval index or model."""
    if transcripts is not None:
        require_input_path(transcripts)
        payload = read_json(transcripts)
        rows = objects(
            payload.get("sources") if isinstance(payload, dict) else None, "sources"
        )
        records = by_key(rows, "source_id", "transcript source")
    else:
        library_dir = data_dir / "library"
        records = {}
        for source_id in gold:
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", source_id):
                raise ContractError(
                    "Stored library source IDs must be YouTube video IDs"
                )
            path = library_dir / "videos" / source_id / "record.json"
            require_private(path)
            records[source_id] = read_json(path)
    verified = {}
    for source_id, row in gold.items():
        record = records.get(source_id)
        transcript = record.get("full_transcript") if isinstance(record, dict) else None
        segments = transcript.get("segments") if isinstance(transcript, dict) else None
        lines = numbered_lines(objects(segments, "transcript segments", nonempty=True))
        if (
            len(lines) != len(segments)
            or fingerprint_lines(lines) != row["source_fingerprint"]
        ):
            raise ContractError(f"Transcript fingerprint mismatch for {source_id}")
        verified[source_id] = record
    return verified
