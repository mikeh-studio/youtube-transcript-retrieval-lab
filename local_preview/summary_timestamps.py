"""Parse timestamps returned by the transcript summary model."""

import math
import re


def parse_summary_timestamp(value: object) -> float | None:
    """Read m:ss, h:mm:ss, or numeric seconds from a summary item."""
    if isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"(?:[0-9]+:[0-5][0-9]|[0-9]+):[0-5][0-9]", value):
            seconds = 0.0
            for part in value.split(":"):
                seconds = seconds * 60 + float(part)
            return seconds if math.isfinite(seconds) else None
        if not re.fullmatch(
            r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value
        ):
            return None
    elif type(value) not in (int, float):
        return None

    try:
        seconds = float(value)
    except (OverflowError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None
