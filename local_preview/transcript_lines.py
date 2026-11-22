"""Stable, one-based line references for canonical transcript segments."""

from __future__ import annotations

import math
from hashlib import sha256

from local_preview.local_files import json_bytes


def numbered_lines(segments):
    """Normalize readable segments, preserving their order in the transcript."""
    lines = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            continue
        text = " ".join(segment["text"].split())
        try:
            start, end = float(segment["start"]), float(segment["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if text and 0 <= start <= end and math.isfinite(end):
            lines.append(
                {"line": len(lines) + 1, "start": start, "end": end, "text": text}
            )
    return lines


def format_lines(lines):
    """Render numbered lines with minute-and-second timecodes."""
    rendered = []
    for row in lines:
        minutes, seconds = divmod(int(row["start"]), 60)
        rendered.append(f"[{row['line']:04d}] {minutes}:{seconds:02d} {row['text']}")
    return "\n".join(rendered)


def line_to_seconds(lines, line):
    """Return a line's onset, or None if the ID is absent."""
    if type(line) is not int:
        return None
    return next((row["start"] for row in lines if row["line"] == line), None)


def seconds_to_line(lines, seconds):
    """Return the latest line starting at or before the timestamp, or None."""
    row = max(
        (row for row in lines if row["start"] <= seconds),
        key=lambda row: (row["start"], row["line"]),
        default=None,
    )
    return row["line"] if row is not None else None


def fingerprint_lines(lines):
    """Hash the numbered transcript representation used for label review."""
    return sha256(json_bytes(lines)).hexdigest()


def transcript_line_map(source):
    """Map every transcript line ID, so references between sampled lines still resolve."""
    return {line["line"]: line for line in source.get("lines") or source["sample"]}
