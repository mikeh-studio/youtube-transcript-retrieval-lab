"""Draft excerpt and timestamp reviews from stored speech-to-text segments."""

import math
import random

from multilingual.source_model import normalize_source


TASK_NAMES = {"stt_reference", "ts_landing"}
DEFAULT_SEED = 42
SAMPLE_SIZE = 10
MAX_TEXT = 4000


def source_details(source_id, record, language):
    """Pin the audio locator and transcription provenance used for review."""
    source = normalize_source(source_id, record.get("source"))
    if source["platform"] not in {"podcast_rss", "local_audio"} or not all(
        source["stt"][field] for field in ("engine", "model")
    ):
        raise ValueError("Choose an audio source with a speech-to-text transcript.")
    if language not in ("en", "ja") or record.get("language") != language:
        raise ValueError("Choose the language of the stored audio transcript.")
    if not (source["local_media_path"] or source["media_url"]):
        raise ValueError("The audio source has no retained media or enclosure URL.")
    return {key: source[key] for key in ("platform", "local_media_path", "media_url", "stt")}


def sample_segments(source, lines, seed):
    """Sample eligible whole segments without a model or provider call."""
    candidates = [
        line for line in lines
        if line["start"] < line["end"] <= source["duration"] and len(line["text"]) <= MAX_TEXT
    ]
    if not candidates:
        raise ValueError("No audio transcript segments are eligible for review.")
    sample = random.Random(seed).sample(candidates, min(SAMPLE_SIZE, len(candidates)))
    return [
        {
            "source_id": source["source_id"],
            "start": line["start"],
            "end": line["end"],
            "text": line["text"],
        }
        for line in sorted(sample, key=lambda line: (line["start"], line["line"]))
    ]


def edit_reference(item, edits, source):
    """Allow an exact transcription, including silence, for the pinned excerpt."""
    if not isinstance(edits, dict) or set(edits) != {"text"}:
        raise ValueError("Edit the excerpt text.")
    text = edits["text"]
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError("Excerpt text may contain up to 4000 characters.")
    return {"text": text.strip()}


def edit_landing(item, edits, source):
    """Record actual onset minus the sampled timestamp in seconds."""
    if not isinstance(edits, dict) or set(edits) != {"offset_seconds"}:
        raise ValueError("Provide an offset in seconds.")
    offset = edits["offset_seconds"]
    if (
        type(offset) not in (int, float)
        or not -item["start"] <= offset <= source["duration"] - item["start"]
        or not math.isfinite(offset)
    ):
        raise ValueError("The offset must place the actual onset within the audio timeline.")
    return {"offset_seconds": offset}
