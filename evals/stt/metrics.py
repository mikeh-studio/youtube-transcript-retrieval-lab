"""Dependency-free speech-to-text error rates."""

import unicodedata


def normalize_text(text, lang="en"):
    """Apply NFKC, lowercase, and remove punctuation and excess whitespace."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    words = "".join(char for char in normalized if not unicodedata.category(char).startswith("P")).split()
    return "".join(words) if lang.lower().split("-", 1)[0] == "ja" else " ".join(words)


def levenshtein(ref, hyp):
    """Count insertions, deletions, and substitutions using one row of memory."""
    if len(ref) < len(hyp):
        ref, hyp = hyp, ref
    previous = list(range(len(hyp) + 1))
    for index, expected in enumerate(ref, start=1):
        current = [index]
        for column, observed in enumerate(hyp, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (expected != observed),
            ))
        previous = current
    return previous[-1]


def wer(ref, hyp):
    """Return word error rate on normalized text, using a floor of one word."""
    words = ref.split()
    return levenshtein(words, hyp.split()) / max(1, len(words))


def cer(ref, hyp):
    """Return character error rate, including spaces in normalized text."""
    return levenshtein(ref, hyp) / max(1, len(ref))
