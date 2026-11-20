"""Compare timestamps using shared unique word trigrams."""

import math
from bisect import bisect_left
from collections import defaultdict
from statistics import median, quantiles

from .metrics import normalize_text


def _anchors(words, lang):
    if not isinstance(words, list):
        raise ValueError("Timed words must be a list.")
    tokens = []
    previous = 0
    for row in words:
        word = row.get("word") if isinstance(row, dict) else None
        start = row.get("start") if isinstance(row, dict) else None
        if not isinstance(word, str) or type(start) not in (int, float) or not previous <= start < math.inf:
            raise ValueError("Timed words need text and finite, nondecreasing start times.")
        previous = start
        token = normalize_text(word, lang)
        if token:
            tokens.append((token, start))
    matches = defaultdict(list)
    for index in range(1, len(tokens) - 1):
        key = tuple(token for token, _ in tokens[index - 1:index + 2])
        matches[key].append((index, tokens[index][1]))
    return {key: hits[0] for key, hits in matches.items() if len(hits) == 1}


def _in_order(pairs):
    # Keep a longest increasing subsequence of STT positions in caption order.
    tails = []
    indices = []
    predecessors = []
    for index, (_, stt_index, _) in enumerate(pairs):
        slot = bisect_left(tails, stt_index)
        predecessors.append(indices[slot - 1] if slot else -1)
        if slot == len(tails):
            tails.append(stt_index)
            indices.append(index)
        else:
            tails[slot] = stt_index
            indices[slot] = index
    chain = []
    index = indices[-1] if indices else -1
    while index >= 0:
        chain.append(pairs[index][2])
        index = predecessors[index]
    return list(reversed(chain))


def align_drift(caption_words, stt_words, lang="en"):
    """Return STT-minus-caption offsets at shared, in-order trigram centers."""
    captions = _anchors(caption_words, lang)
    stt = _anchors(stt_words, lang)
    pairs = []
    for key, (caption_index, caption_start) in captions.items():
        if key in stt:
            stt_index, stt_start = stt[key]
            pairs.append((caption_index, stt_index, stt_start - caption_start))
    offsets = _in_order(sorted(pairs))
    iqr = None
    if len(offsets) == 1:
        iqr = 0.0
    elif offsets:
        first, _, third = quantiles(offsets, n=4, method="inclusive")
        iqr = third - first
    return {
        "anchor_count": len(offsets),
        "offsets_seconds": offsets,
        "median_seconds": median(offsets) if offsets else None,
        "iqr_seconds": iqr,
    }
