"""Sweep collection clustering thresholds using cached key areas and local embeddings."""

import argparse
import json
import math
import os
from pathlib import Path

from evals.contracts import ROOT, read_json, require_private
from local_preview.collection_summary import (
    cluster_collection_sections,
    collection_embedding_fn,
    collection_sections,
    collection_source_ids,
    embed_collection_sections,
)
from local_preview.key_areas import KEY_AREAS_VERSION
from local_preview.local_files import json_bytes, write_atomic
from multilingual.source_model import SOURCE_ID_RE


DEFAULT_THRESHOLDS = (0.80, 0.85, 0.88, 0.90)


def sweep_clusters(source_ids, *, key_areas_fn, embed_fn, thresholds=DEFAULT_THRESHOLDS):
    """Measure ranked cluster membership at each threshold from one embedding batch."""
    sections = collection_sections(collection_source_ids(source_ids), key_areas_fn)
    try:
        vectors = embed_collection_sections(sections, embed_fn)
    except Exception as exc:
        raise RuntimeError("Local section embedding failed") from exc
    results = []
    for threshold in thresholds:
        clusters = cluster_collection_sections(sections, vectors, min_cosine=threshold)
        cluster_source_ids = [
            sorted({section["source_id"] for section in cluster}) for cluster in clusters
        ]
        results.append({
            "threshold": threshold,
            "clusters": len(clusters),
            "cluster_sizes": [len(cluster) for cluster in clusters],
            "distinct_sources": [len(ids) for ids in cluster_source_ids],
            "cluster_source_ids": cluster_source_ids,
        })
    return {"sections": len(sections), "results": results}


def _read_cached_key_areas(data_dir, source_id, *, language, provider, model):
    if not SOURCE_ID_RE.fullmatch(source_id):
        raise ValueError("Invalid source ID in --source-ids")
    entries = read_json(data_dir / "cache" / "key_areas" / f"{source_id}.json")
    if not isinstance(entries, dict):
        raise ValueError(f"Invalid key-area cache for {source_id}")

    matches = []
    for cache_key, entry in entries.items():
        try:
            key = json.loads(cache_key)
        except ValueError:
            continue
        # Non-YouTube entries also include the playback URL in their key.
        if not isinstance(key, list) or len(key) not in (5, 6):
            continue
        version, cached_language, cached_provider, cached_model, fingerprint = key[:5]
        if (
            version == KEY_AREAS_VERSION and cached_language == language
            and (provider is None or cached_provider == provider)
            and (model is None or cached_model == model)
            and all(
                isinstance(value, str) for value in (cached_provider, cached_model, fingerprint)
            )
        ):
            matches.append((entry, {
                "source_id": source_id,
                "provider": cached_provider,
                "model": cached_model,
                "source_fingerprint": fingerprint,
            }))
    if not matches:
        raise ValueError(f"No matching current key-area cache entry for {source_id}")
    if len(matches) != 1:
        raise ValueError(
            f"Multiple key-area cache entries match {source_id}. "
            "Select --provider and --model or use a data directory with one matching entry."
        )
    entry, metadata = matches[0]
    sections = entry.get("sections") if isinstance(entry, dict) else None
    if not isinstance(sections, list) or not sections or any(
        not isinstance(section, dict)
        or not all(isinstance(section.get(key), str) for key in ("title", "summary"))
        or not all(
            type(section.get(key)) in (int, float) and math.isfinite(section[key])
            for key in ("start", "end")
        )
        or not 0 <= section["start"] <= section["end"]
        for section in sections
    ):
        raise ValueError(f"Invalid cached key-area sections for {source_id}")
    return entry, metadata


def _local_processor():
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        from multilingual.text_processing import TextProcessor
    except ImportError as exc:
        raise RuntimeError("The local embedding dependencies are unavailable") from exc
    processor = TextProcessor()
    if processor.embedding_backend != "sentence_transformers":
        raise RuntimeError(
            "The configured embedding model could not load locally. "
            "Use the app's model settings and an existing model snapshot."
        )
    return processor


def _thresholds(value):
    try:
        numbers = tuple(float(item) for item in value.split(","))
    except ValueError:
        numbers = ()
    if not numbers or any(not -1 <= number <= 1 for number in numbers):
        raise argparse.ArgumentTypeError("expected comma-separated cosine thresholds in [-1, 1]")
    return numbers


def main(argv=None, *, embed_fn=None):
    """Write an aggregate JSON report for each requested source set."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument(
        "--source-ids", action="append", required=True, help="Comma-separated IDs, repeatable"
    )
    parser.add_argument("--language", choices=("en", "ja"), default="en")
    parser.add_argument("--thresholds", type=_thresholds, default=DEFAULT_THRESHOLDS)
    parser.add_argument("--provider", help="Filter by the cached provider")
    parser.add_argument("--model", help="Filter by the cached model")
    parser.add_argument("--out", type=Path, required=True, help="JSON file under evals/reports/")
    args = parser.parse_args(argv)

    try:
        out = args.out.resolve()
        if not out.is_relative_to(ROOT.resolve() / "evals" / "reports"):
            raise ValueError("--out must be under evals/reports/")
        require_private(out)
        source_sets = [collection_source_ids(value.split(",")) for value in args.source_ids]
        cached = {
            source_id: _read_cached_key_areas(
                args.data_dir, source_id, language=args.language,
                provider=args.provider, model=args.model,
            )
            for source_id in dict.fromkeys(source_id for ids in source_sets for source_id in ids)
        }
        metadata = None
        if embed_fn is None:
            processor = _local_processor()
            embed_fn = collection_embedding_fn(processor)
            metadata = processor.embedding_metadata()
        report = {
            "schema": "collection-cluster-sweep/v1",
            "language": args.language,
            "key_areas_version": KEY_AREAS_VERSION,
            "embedding": metadata,
            "sets": [
                {
                    "source_ids": ids,
                    "cache_entries": [cached[source_id][1] for source_id in ids],
                    **sweep_clusters(
                        ids, key_areas_fn=lambda source_id: cached[source_id][0],
                        embed_fn=embed_fn, thresholds=args.thresholds,
                    ),
                }
                for ids in source_sets
            ],
        }
        write_atomic(out, json_bytes(report))
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"collection cluster sweep: {exc}\n")


if __name__ == "__main__":
    main()
