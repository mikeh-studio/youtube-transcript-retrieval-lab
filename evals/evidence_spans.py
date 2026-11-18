"""Canonical timestamp labels for the retrieval benchmark."""

from __future__ import annotations

import math


def _timestamp_fields(row: dict) -> tuple:
    bounds = row.get("timestamp_range")
    if not isinstance(bounds, dict):
        bounds = row
    return (
        bounds.get("start", bounds.get("start_seconds")),
        bounds.get("end", bounds.get("end_seconds")),
    )


def _timestamp_bounds(row: dict) -> tuple[float, float] | None:
    raw_start, raw_end = _timestamp_fields(row)
    try:
        start, end = float(raw_start), float(raw_end)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(start) and math.isfinite(end) and start <= end):
        return None
    return (
        raw_start if type(raw_start) is int else start,
        raw_end if type(raw_end) is int else end,
    )


def _integer(value, *, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{field} must be an integer") from None
    if isinstance(value, bool) or (not isinstance(value, str) and value != number):
        raise ValueError(f"{field} must be an integer")
    return number


def chunking_version(row: dict) -> str | None:
    """Read version metadata from a flat field or a chunking descriptor."""
    chunking = row.get("chunking")
    version = row.get("chunking_version")
    if not version and isinstance(chunking, dict):
        version = chunking.get("version")
    return str(version) if version else None


def normalize_gold_evidence(row: dict) -> dict:
    """Validate a gold evidence row and emit canonical field names."""
    if not isinstance(row, dict):
        raise ValueError("gold evidence must be an object")
    video_id = row.get("video_id") or row.get("source_id")
    if not video_id:
        raise ValueError("gold evidence requires video_id or source_id")
    normalized = {"video_id": str(video_id)}

    start, end = _timestamp_fields(row)
    if start is not None or end is not None:
        bounds = _timestamp_bounds(row)
        if bounds is None:
            raise ValueError("gold evidence requires finite start and end with start <= end")
        normalized["start"], normalized["end"] = bounds

    legacy_index = row.get("legacy_chunk_index")
    if legacy_index is None:
        legacy_index = row.get("chunk_index")
    if legacy_index is not None:
        index = _integer(legacy_index, field="legacy_chunk_index")
        if index < 0:
            raise ValueError("legacy_chunk_index must be non-negative")
        normalized["legacy_chunk_index"] = index
    if "start" not in normalized and "legacy_chunk_index" not in normalized:
        raise ValueError("gold evidence requires a timestamp span or legacy chunk index")

    version = chunking_version(row)
    if version is not None:
        normalized["chunking_version"] = version
    if row.get("grade") is not None:
        normalized["grade"] = _integer(row["grade"], field="grade")
    return normalized


def validate_overlap_ratio(value) -> float:
    """Read a numeric overlap threshold between zero and one."""
    try:
        ratio = float(value)
    except (TypeError, ValueError, OverflowError):
        ratio = math.nan
    if not 0 <= ratio <= 1:
        raise ValueError("span_min_overlap_ratio must be between 0 and 1")
    return ratio


def span_matches(gold: dict, result: dict, min_overlap_ratio: float = 0.5) -> bool:
    """Compare the timestamps of two rows that refer to the same source."""
    ratio = validate_overlap_ratio(min_overlap_ratio)
    gold_bounds, result_bounds = _timestamp_bounds(gold), _timestamp_bounds(result)
    if gold_bounds is None or result_bounds is None:
        return False
    gold_start, gold_end = gold_bounds
    result_start, result_end = result_bounds
    if gold_start == gold_end:
        return result_start <= gold_start <= result_end
    shorter_length = min(gold_end - gold_start, result_end - result_start)
    overlap = max(0.0, min(gold_end, result_end) - max(gold_start, result_start))
    return shorter_length > 0 and overlap / shorter_length >= ratio


def _corpus_chunk(video: dict, index: int) -> dict | None:
    """Find a chunk by its explicit chunk_index, falling back to list position."""
    chunks = video.get("chunks") or []
    explicit = [chunk for chunk in chunks if chunk.get("chunk_index") is not None]
    if explicit:
        for chunk in explicit:
            try:
                if int(chunk["chunk_index"]) == index:
                    return chunk
            except (TypeError, ValueError):
                continue
        return None
    return chunks[index] if 0 <= index < len(chunks) else None


def chunk_rows_to_spans(rows: list[dict], corpus: dict) -> list[dict]:
    """Convert dataset rows using the source corpus's per-video chunk order."""
    videos = {str(video["video_id"]): video for video in corpus["videos"]}
    migrated = []
    for row in rows:
        evidence = []
        for raw_gold in row["gold_evidence"]:
            gold = normalize_gold_evidence(raw_gold)
            if "start" not in gold:
                video_id, index = gold["video_id"], gold["legacy_chunk_index"]
                video = videos.get(video_id)
                chunk = _corpus_chunk(video, index) if video is not None else None
                if chunk is None:
                    raise ValueError(
                        f"Corpus is missing chunk {index} for {video_id}"
                    )
                version = chunking_version(video) or chunking_version(corpus)
                gold_version = gold.get("chunking_version")
                if gold_version and version and gold_version != version:
                    raise ValueError(
                        f"Chunking version mismatch for {video_id}: "
                        f"gold {gold_version!r}, corpus {version!r}"
                    )
                bounds = _timestamp_bounds(chunk)
                if bounds is None:
                    raise ValueError(
                        f"Corpus chunk {index} for {video_id} has invalid timestamps"
                    )
                gold = normalize_gold_evidence({
                    **gold,
                    "start": bounds[0],
                    "end": bounds[1],
                    "chunking_version": version or gold_version,
                })
            evidence.append(gold)
        migrated.append({**row, "gold_evidence": evidence})
    return migrated
