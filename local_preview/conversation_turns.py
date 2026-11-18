"""Identify explicit speaker cues in numbered transcript lines."""

import re
import unicodedata


_ENGLISH_NAME = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
_JAPANESE_NAME = r"[\u3400-\u9fff\u3041-\u30fa々〆・ー ]{2,24}"
_JAPANESE_PREFIX = re.compile(rf"^(?:\(({_JAPANESE_NAME})\)\s*|({_JAPANESE_NAME}):\s*)")
_STAGE_CUES = {"拍手", "笑", "笑い", "笑い声", "音楽", "歓声", "効果音", "無音"}


def _speaker_prefix(text, japanese):
    if japanese:
        match = _JAPANESE_PREFIX.match(text)
        if match:
            name = (match[1] or match[2]).strip()
            if name not in _STAGE_CUES and len(name) >= 2:
                return name

    prefix, colon, _ = text.partition(":")
    tokens = prefix.split()
    if (
        colon
        and 2 <= len(tokens) <= 3
        and all(
            token[0].isupper() and _ENGLISH_NAME.fullmatch(token) for token in tokens
        )
    ):
        return " ".join(tokens)
    return None


def detect_turns(lines, language):
    """Return one turn record per line, carrying labels until the next cue."""
    turn_id = 0
    speaker = None
    turns = []
    japanese = language.lower().split("-", 1)[0] == "ja"
    for line in lines:
        text = unicodedata.normalize("NFKC", line["text"]).lstrip()
        marker = text.startswith(">>")
        label = _speaker_prefix(text[2:].lstrip() if marker else text, japanese)
        if not turns or marker or label is not None:
            turn_id += 1
            speaker = label
        turns.append(
            {
                "turn_id": turn_id,
                "speaker_label": speaker,
                "cue": "name_prefix"
                if label
                else "marker"
                if marker
                else "continuation",
            }
        )
    return turns
