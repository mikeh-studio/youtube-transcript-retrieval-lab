"""Adapt summary responses to predictions and validate their scoring contract."""

import json
from hashlib import sha256

from evals.contracts import ContractError
from evals.summary.dataset import by_key, objects, read_jsonl, span, text
from local_preview.local_files import json_bytes


TIMESTAMP_SOURCES = ("anchor_match", "model_fallback")


def record_sha256(row):
    """Hash JSON content independent of object key order."""
    canonical = json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def generate(gold, summarize, *, provider, model=None, system=None):
    """Call the injected summarizer once per source and build prediction rows."""
    rows = []
    for source_id, source in gold.items():
        result = summarize(
            video_id=source_id,
            language=source["language"],
            provider=provider,
            model=model,
        )
        items = []
        for index, item in enumerate(result["summary"], start=1):
            item_text = " ".join(
                part for part in (item.get("title"), item.get("tldr")) if part
            )
            timestamp_source = item.get("timestamp_source")
            segment = {"start": item["start"], "end": item["end"]}
            items.append(
                {
                    "id": f"item_{index}",
                    "text": item_text,
                    **segment,
                    "timestamp_source": timestamp_source,
                    "segment_index": item.get("segment_index"),
                    "evidence": [segment] if timestamp_source == "anchor_match" else [],
                }
            )
        resolved_model = result["model"]
        rows.append(
            {
                "source_id": source_id,
                "source_fingerprint": source["source_fingerprint"],
                "system": system or f"{result['provider']}:{resolved_model}",
                "language": result["language"],
                "provider": result["provider"],
                "model": resolved_model,
                "cli_version": result.get("cli_version"),
                "cache_hit": result["generation_details"]["cache_hit"],
                "items": items,
            }
        )
    validate_predictions(rows, gold)
    return rows


def validate_predictions(rows, gold):
    """Validate known sources, unique IDs and coverage of every source by each system."""
    indexed = {}
    systems = set()
    for row in rows:
        source_id = text(row.get("source_id"), "prediction source_id")
        system = text(row.get("system"), "prediction system")
        key = (system, source_id)
        if source_id not in gold or key in indexed:
            raise ContractError(
                "Predictions must reference known sources once per system"
            )
        source = gold[source_id]
        if (
            row.get("source_fingerprint", source["source_fingerprint"])
            != source["source_fingerprint"]
        ):
            raise ContractError("Prediction source fingerprint does not match gold")
        text(row.get("language"), "prediction language")
        for item in by_key(
            objects(row.get("items"), "items"), "id", "prediction item"
        ).values():
            text(item.get("text"), "prediction text")
            span(item, "prediction", source["duration_s"])
            if item.get("timestamp_source") not in (*TIMESTAMP_SOURCES, None):
                raise ContractError("Invalid prediction timestamp_source")
            segment_index = item.get("segment_index")
            if segment_index is not None and (
                type(segment_index) is not int or segment_index < 0
            ):
                raise ContractError(
                    "Prediction segment_index must be a nonnegative integer or null"
                )
            for evidence in objects(item.get("evidence"), "evidence"):
                span(evidence, "prediction evidence", source["duration_s"])
        indexed[key] = row
        systems.add(system)
    if not systems or any(
        (system, source_id) not in indexed for system in systems for source_id in gold
    ):
        raise ContractError("Each prediction system must cover every gold source")
    return indexed


def load_predictions(path, gold):
    return validate_predictions(read_jsonl(path), gold)


def encode_rows(rows):
    return b"".join(json_bytes(row) for row in rows)
