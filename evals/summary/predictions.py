"""Adapt summary responses to predictions and validate their scoring contract."""

import json
from hashlib import sha256

from evals.contracts import ContractError
from evals.summary.dataset import by_key, objects, read_jsonl, span, text
from local_preview.local_files import json_bytes


RESOLVED_TIMESTAMP_SOURCES = ("anchor_match", "line_ids")
TIMESTAMP_SOURCES = (*RESOLVED_TIMESTAMP_SOURCES, "model_fallback")


def record_sha256(row):
    """Hash JSON content independent of object key order."""
    canonical = json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _key_area_items(sections):
    points = (point for section in sections for point in section["key_points"])
    return [
        {**point, "id": f"item_{index}", "timestamp_source": "line_ids"}
        for index, point in enumerate(points, start=1)
    ]


def _summary_items(summary):
    items = []
    for index, item in enumerate(summary, start=1):
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
    return items


def generate(gold, summarize, *, provider, model=None, system=None):
    """Call the injected summarizer once per source and build prediction rows."""
    rows = []
    key_areas = system == "key_areas"
    for source_id, source in gold.items():
        identifier = {"source_id" if key_areas else "video_id": source_id}
        result = summarize(
            **identifier,
            language=source["language"],
            provider=provider,
            model=model,
        )
        if key_areas:
            fields = {
                "source_fingerprint": result["source_fingerprint"],
                "items": _key_area_items(result["sections"]),
                "sections": result["sections"],
            }
        else:
            fields = {
                "source_fingerprint": source["source_fingerprint"],
                "items": _summary_items(result["summary"]),
            }
        resolved_model = result["model"]
        rows.append(
            {
                "source_id": source_id,
                "system": system or f"{result['provider']}:{resolved_model}",
                "language": result["language"],
                "provider": result["provider"],
                "model": resolved_model,
                "cli_version": result.get("cli_version"),
                "cache_hit": result["generation_details"]["cache_hit"],
                **fields,
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
        if "sections" in row:
            for section in objects(row["sections"], "prediction sections"):
                text(section.get("title"), "prediction section title")
                span(section, "prediction section", source["duration_s"])
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
