"""Validate a saved summary judge audit sample for owner review."""

from evals.contracts import ContractError, SHA256
from evals.summary.dataset import by_key, objects, span, text
from evals.summary.judge import FAITHFULNESS_LABELS, validate_window


def validate_sample(rows):
    """Check decision identity and the context needed to review each item."""
    rows = objects(rows, "audit sample", nonempty=True)
    if len(rows) > 20:
        raise ContractError("An audit sample may contain at most 20 decisions")
    sources = {}
    seen = set()
    for row in rows:
        if row.get("schema") != "summary-judge-audit/v1":
            raise ContractError("Expected a summary-judge-audit/v1 sample")
        source_id = text(row.get("source_id"), "audit source_id")
        system = text(row.get("system"), "audit system")
        item_id = text(row.get("item_id"), "audit item_id")
        kind = row.get("kind")
        if kind not in ("match", "faithfulness") or "decision" not in row:
            raise ContractError("Unknown audit decision")
        key = (source_id, system, item_id, kind)
        if key in seen:
            raise ContractError("Audit decisions must be unique")
        seen.add(key)
        if row.get("language") not in ("en", "ja"):
            raise ContractError("Invalid audit source language")
        for field in ("source_fingerprint", "prediction_sha256", "gold_sha256"):
            if not SHA256.fullmatch(str(row.get(field, ""))):
                raise ContractError(f"Invalid audit {field}")
        fingerprint = row["source_fingerprint"]
        if sources.setdefault(source_id, fingerprint) != fingerprint:
            raise ContractError("Audit contains multiple versions of a source")
        text(row.get("prediction_text"), "audit prediction")
        span(row, "audit prediction")
        provenance = row.get("judge")
        if not isinstance(provenance, dict):
            raise ContractError("Invalid audit judge provenance")
        text(provenance.get("provider"), "audit judge provider")
        text(provenance.get("model"), "audit judge model")
        decision = row["decision"]
        if kind == "match":
            points = by_key(
                objects(row.get("gold_points"), "audit gold points", nonempty=True),
                "id",
                "audit gold point",
            )
            for point in points.values():
                text(point.get("text"), "audit gold point text")
            if decision is not None and (
                not isinstance(decision, str) or decision not in points
            ):
                raise ContractError("Unknown audit gold point")
        else:
            if decision not in FAITHFULNESS_LABELS:
                raise ContractError("Invalid audit faithfulness decision")
            if not validate_window(row.get("window")) and decision != "unsupported":
                raise ContractError("An empty evidence window requires unsupported")
    return sources
