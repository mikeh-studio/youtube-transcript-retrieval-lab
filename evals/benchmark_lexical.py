"""Offline BM25 cold/warm benchmark with a frozen pre-cache scoring baseline."""

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
from statistics import median
import time

from local_preview.lexical_index import LexicalIndex


def uncached_search(candidates, query_tokens, *, k, tokenize):
    """Original BM25 calculation retained independently as a parity oracle."""
    if not candidates or not query_tokens or k <= 0:
        return []
    documents = [
        tokenize(row["text"], language=row.get("language")) for row in candidates
    ]
    lengths = [len(tokens) for tokens in documents]
    df = Counter()
    for tokens in documents:
        df.update(set(tokens))
    average = sum(lengths) / len(candidates)
    scored = []
    for row, tokens in zip(candidates, documents):
        tf = Counter(tokens)
        score = 0.0
        for term, weight in Counter(query_tokens).items():
            if term not in tf:
                continue
            idf = math.log(1.0 + (len(candidates) - df[term] + 0.5) / (df[term] + 0.5))
            denominator = tf[term] + 1.5 * (
                1.0 - 0.75 + 0.75 * (max(1, len(tokens)) / max(average, 1e-9))
            )
            score += idf * ((tf[term] * 2.5) / denominator) * (1.0 + 0.2 * (weight - 1))
        if score > 0:
            scored.append(dict(row, lexical_score=float(score), score=float(score)))
    scored.sort(key=lambda row: row["lexical_score"], reverse=True)
    for rank, row in enumerate(scored, 1):
        row["rank"] = rank
    return scored[:k]


def benchmark(*, chunks, runs, tokenize, token_version):
    """Compare identical synthetic workloads; fail on any score or citation drift."""
    if chunks < 1 or runs < 1:
        raise ValueError("chunks and runs must be positive")
    candidates = [
        {
            "video_id": f"video-{index // 20}",
            "chunk_index": index % 20,
            "language": "ja" if index % 2 else "en",
            "text": (
                f"機械学習 推論 エッジAI Jetson Orin 実験 {index} 条件 {index % 11}"
                if index % 2
                else f"Machine learning inference edge AI Jetson Orin experiment {index} condition {index % 11}"
            ),
            "start": index * 10,
            "end": index * 10 + 10,
        }
        for index in range(chunks)
    ]
    queries = [
        tokenize(text, language=language)
        for text, language in (
            ("Jetson Orin inference", "en"),
            ("機械学習 推論", "ja"),
            ("experiment 3", "en"),
            ("存在しない用語", "ja"),
        )
    ]
    timings = {name: [] for name in ("uncached", "cached_cold", "cached_warm")}
    index = LexicalIndex(tokenize)
    for _ in range(runs):
        for query in queries:
            start = time.perf_counter()
            expected = uncached_search(candidates, query, k=12, tokenize=tokenize)
            timings["uncached"].append((time.perf_counter() - start) * 1000)
            index.clear()
            for name in ("cached_cold", "cached_warm"):
                start = time.perf_counter()
                result = index.search(
                    candidates, query, k=12, token_version=token_version
                )
                timings[name].append((time.perf_counter() - start) * 1000)
                if result != expected:
                    raise AssertionError(f"BM25 parity failed in {name}")
    metrics = {
        name: {
            "p50_ms": round(median(samples), 3),
            "p95_ms": round(sorted(samples)[math.ceil(len(samples) * 0.95) - 1], 3),
            "samples": len(samples),
        }
        for name, samples in timings.items()
    }
    return {
        "workload": "synthetic bilingual BM25 kernel; excludes API, embeddings, and answers",
        "chunks": chunks,
        "runs": runs,
        "tokenizer_version": token_version,
        "exact_result_parity": True,
        "metrics": metrics,
        "warm_speedup": round(
            median(timings["uncached"]) / median(timings["cached_warm"]), 2
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.environ["YT_RAG_SKIP_GLOBAL_SERVICE"] = "1"
    # Reuse the actual tokenizer without constructing a service or loading models.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_preview"))
    from local_api import lexical_token_version, tokenize_for_lexical

    result = benchmark(
        chunks=args.chunks,
        runs=args.runs,
        tokenize=tokenize_for_lexical,
        token_version=lexical_token_version(),
    )
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
