"""Re-embed every transcript chunk and rewrite the library index.

Adding a source only embeds that source's chunks, so vectors written by
earlier adds are never refreshed. Run this after a batch ingest, or after
changing passage formatting, to put the whole index back on one embedding pass.

Stop the local app (and any ingest) first: both write the same index files.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from multilingual.video_library import VideoLibrary

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _describe(metadata):
    if not isinstance(metadata, dict) or not metadata:
        return "none"
    parts = [str(metadata.get("backend")), str(metadata.get("model"))]
    parts.append(f"dim {metadata.get('dim')}")
    if metadata.get("revision"):
        parts.append(f"revision {metadata['revision']}")
    return ", ".join(parts)


def reembed(library, *, allow_hash=False, dry_run=False, log=print):
    """Rebuild ``library``'s index from its stored chunks and save it.

    Returns a dict with ``status`` ("rebuilt", "dry_run" or "refused"),
    ``sources``, ``chunks``, ``vectors``, ``embedding`` and ``seconds``.
    """
    current = library.processor.embedding_metadata()
    saved = library.index_embedding_metadata
    result = {
        "status": "dry_run" if dry_run else "rebuilt",
        "sources": len(library.videos),
        "chunks": library.total_chunks,
        "vectors": 0,
        "embedding": current,
        "seconds": 0.0,
    }
    log(f"Library: {result['sources']} sources, {result['chunks']} chunks")
    log(f"Saved index embedding: {_describe(saved)}")
    log(f"Active embedding:      {_describe(current)}")

    if current.get("backend") != "sentence_transformers" and not allow_hash:
        log(
            "Refusing to re-embed with local hashing embeddings: the embedding "
            "model is not loaded. Fix the model setup, or pass --allow-hash "
            "to build a hashing index on purpose."
        )
        result["status"] = "refused"
        return result
    if dry_run:
        return result

    started = time.perf_counter()
    library._rebuild_index()
    library.save()
    result["seconds"] = time.perf_counter() - started
    result["vectors"] = int(library.index.ntotal) if library.index is not None else 0
    log(f"Re-embedded {result['vectors']} vectors in {result['seconds']:.1f}s and saved.")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        help="Library data directory (default: the repository's data/).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report sources, chunks and embedding settings without embedding.",
    )
    parser.add_argument(
        "--allow-hash",
        action="store_true",
        help="Allow a rebuild with local hashing embeddings (offline/testing).",
    )
    args = parser.parse_args(argv)

    if not Path(args.data_dir).is_dir():
        print(f"No library data directory at {args.data_dir}", file=sys.stderr)
        return 1
    library = VideoLibrary(data_dir=args.data_dir)
    result = reembed(library, allow_hash=args.allow_hash, dry_run=args.dry_run)
    return 2 if result["status"] == "refused" else 0


if __name__ == "__main__":
    sys.exit(main())
