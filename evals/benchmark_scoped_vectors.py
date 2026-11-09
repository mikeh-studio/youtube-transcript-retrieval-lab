"""Offline scoped-vector parity and performance benchmark using real FAISS."""

import argparse
from copy import copy
import json
import math
from pathlib import Path
from statistics import median
import time
from types import SimpleNamespace

import numpy as np

from multilingual.video_library import VideoLibrary, faiss


def benchmark(*, vectors=10000, dimension=768, videos=100, runs=5, top_k=10):
    """Measure full search/filter against selected-vector search on identical data."""
    if min(vectors, dimension, videos, runs, top_k) < 1 or videos > vectors:
        raise ValueError("sizes must be positive and videos cannot exceed vectors")
    faiss.omp_set_num_threads(1)
    rng = np.random.default_rng(20261028)
    embeddings = rng.standard_normal((vectors, dimension), dtype=np.float32)
    embeddings[::17] = embeddings[0]  # Exercise duplicates and cutoff ties.
    queries = rng.standard_normal((4, dimension), dtype=np.float32)
    queries[0] = embeddings[0]
    queries[1] = 0  # Every vector ties; ordering must still match.
    library = VideoLibrary.__new__(VideoLibrary)
    library.index = faiss.IndexFlatIP(dimension)
    library.index.add(embeddings)
    library.processor = SimpleNamespace(
        encode_query=lambda query, language=None: queries[int(query) : int(query) + 1]
    )
    library.videos = {
        f"video-{i}": {
            "title": f"Synthetic video {i}",
            "url": f"https://example.invalid/{i}",
            "language": "ja" if i % 2 else "en",
            "chunks": [],
        }
        for i in range(videos)
    }
    library.chunk_map = []
    for i in range(vectors):
        video_id = f"video-{i % videos}"
        chunks = library.videos[video_id]["chunks"]
        library.chunk_map.append((video_id, len(chunks)))
        chunks.append(
            {"raw_text": f"Synthetic evidence {i}", "start": i * 3, "end": i * 3 + 3}
        )
    baseline = copy(library)
    baseline._search_scoped_index = lambda query, ids: baseline.index.search(
        query, vectors
    )

    cases = []
    for scope_size in sorted({1, max(1, videos // 20), max(1, videos // 2), videos}):
        scope = list(library.videos)[:scope_size]
        selected_count = sum(
            video_id in set(scope) for video_id, _ in library.chunk_map
        )
        samples = {"full_search_filter": [], "selected_vectors": []}
        # Warm each code path before timing; there is no added index/cache build.
        for service in (baseline, library):
            service.search("0", k=top_k, video_ids=scope, language="en")
        for run in range(runs):
            for query in range(len(queries)):
                outcomes = {}
                # Alternate measurement order to reduce systematic order bias.
                order = (
                    ("full_search_filter", baseline),
                    ("selected_vectors", library),
                )
                if run % 2:
                    order = tuple(reversed(order))
                for name, service in order:
                    started = time.perf_counter()
                    outcomes[name] = service.search(
                        str(query), k=top_k, video_ids=scope, language="en"
                    )
                    samples[name].append((time.perf_counter() - started) * 1000)
                if outcomes["full_search_filter"] != outcomes["selected_vectors"]:
                    raise AssertionError(
                        f"Exact result parity failed for {scope_size} videos, query {query}"
                    )
        cases.append(
            {
                "selected_videos": scope_size,
                "selected_vectors": selected_count,
                "exact_result_parity": True,
                "metrics": {
                    name: {
                        "p50_ms": round(median(values), 3),
                        "p95_ms": round(
                            sorted(values)[math.ceil(len(values) * 0.95) - 1], 3
                        ),
                        "samples": len(values),
                    }
                    for name, values in samples.items()
                },
                "speedup": round(
                    median(samples["full_search_filter"])
                    / median(samples["selected_vectors"]),
                    2,
                ),
            }
        )
    return {
        "workload": "synthetic FAISS vectors; includes scope-ID selection and result formatting; excludes embedding inference and ingestion",
        "faiss_version": faiss.__version__,
        "faiss_threads": 1,
        "vectors": vectors,
        "dimension": dimension,
        "videos": videos,
        "runs": runs,
        "top_k": top_k,
        "exact_result_parity": True,
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", type=int, default=10000)
    parser.add_argument("--dimension", type=int, default=768)
    parser.add_argument("--videos", type=int, default=100)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = benchmark(
        vectors=args.vectors,
        dimension=args.dimension,
        videos=args.videos,
        runs=args.runs,
        top_k=args.top_k,
    )
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
