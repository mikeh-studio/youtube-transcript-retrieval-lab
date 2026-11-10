"""Offline, ungraded retrieval timing on a frozen private library snapshot.

Only transcript records are copied into a temporary data directory. The saved
index is deliberately excluded because its model revision may be unknown.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODES = ("dense", "lexical", "hybrid")
CONFIG = {
    "k": 5,
    "modes": list(MODES),
    "retrieval_profile": "baseline_rrf",
    "reranker": "none",
    "feedback_tuning": False,
    "warm_repetitions": 3,
    "probe_version": 1,
}


def canonical_hash(value: object) -> str:
    """Hash canonical JSON without including machine-specific paths."""
    payload = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_hashes(data_dir: Path) -> dict[str, str]:
    """Hash exactly the records selected by the library manifest."""
    records = data_dir / "library" / "videos"
    if not records.is_dir():
        raise ValueError("snapshot must contain library/videos")
    manifest_path = data_dir / "library" / "library.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("snapshot must contain a regular library manifest")
    selected = json.loads(manifest_path.read_text(encoding="utf-8")).get("videos")
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(value, str) or not value for value in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("library manifest has invalid video IDs")
    paths = sorted(records.glob("*/record.json"))
    if not paths:
        raise ValueError("snapshot contains no video records")
    if set(selected) != {path.parent.name for path in paths}:
        raise ValueError("manifest video IDs differ from record set")
    hashes = {}
    for path in paths:
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("symlinked records are not permitted")
        video_id = path.parent.name
        if not video_id or video_id in hashes:
            raise ValueError("invalid or duplicate video ID")
        hashes[video_id] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def copy_records(source: Path, destination: Path) -> dict[str, str]:
    """Copy only raw records, never indexes or runtime state."""
    hashes = record_hashes(source)
    target_manifest = destination / "library" / "library.json"
    target_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / "library" / "library.json", target_manifest)
    for video_id in hashes:
        source_path = source / "library" / "videos" / video_id / "record.json"
        target = destination / "library" / "videos" / video_id / "record.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
    if (
        record_hashes(destination) != hashes
        or record_hashes(source) != hashes
        or _manifest_hash(destination) != _manifest_hash(source)
    ):
        raise RuntimeError("record content changed during copy")
    return hashes


def _manifest_hash(data_dir: Path) -> str:
    return hashlib.sha256(
        (data_dir / "library" / "library.json").read_bytes()
    ).hexdigest()


def load_records(data_dir: Path) -> dict[str, dict]:
    videos = {}
    for path in sorted((data_dir / "library" / "videos").glob("*/record.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        chunks = record.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise ValueError("every video must have nonempty chunks")
        if record.get("language") not in {"en", "ja"}:
            raise ValueError("unsupported record language")
        if not isinstance(record.get("title"), str) or not record["title"]:
            raise ValueError("video title is missing")
        if not isinstance(record.get("url"), str) or not record["url"]:
            raise ValueError("video URL is missing")
        for chunk in chunks:
            if not isinstance(chunk, dict) or any(
                not isinstance(chunk.get(key), str) or not chunk[key].strip()
                for key in ("raw_text", "embed_text")
            ):
                raise ValueError("chunk text is missing")
            try:
                start, end = float(chunk["start"]), float(chunk["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("chunk timestamps are invalid") from exc
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
                raise ValueError("chunk timestamps are invalid")
        videos[path.parent.name] = record
    return videos


def generated_workload(videos: dict[str, dict]) -> dict:
    """Freeze six bounded source-derived probes; these have no relevance labels."""
    queries = []
    for language in ("en", "ja"):
        ids = sorted(
            video_id
            for video_id, video in videos.items()
            if video["language"] == language
        )
        if len(ids) < 3:
            raise ValueError(
                "generated probes require at least three videos per language"
            )
        for ordinal, video_id in enumerate((ids[0], ids[len(ids) // 2], ids[-1]), 1):
            chunks = videos[video_id]["chunks"]
            chunk = chunks[len(chunks) // 2]
            text = " ".join(str(chunk["raw_text"]).split())[:120].strip()
            if len(text) < 12:
                raise ValueError("source-derived probe is too short")
            scope = (
                {"kind": "global"}
                if ordinal == 1
                else {"kind": "video", "video_id": video_id}
            )
            queries.append(
                {
                    "id": f"{language}-{ordinal}",
                    "text": text,
                    "language": language,
                    "scope": scope,
                }
            )
    return {"schema": "ungraded_retrieval_workload_v1", "queries": queries}


def validate_workload(workload: dict, videos: dict[str, dict]) -> None:
    if (
        set(workload) != {"schema", "queries"}
        or workload["schema"] != "ungraded_retrieval_workload_v1"
    ):
        raise ValueError("workload must use ungraded_retrieval_workload_v1")
    queries = workload["queries"]
    if not isinstance(queries, list) or not queries:
        raise ValueError("workload queries must be nonempty")
    seen = set()
    for query in queries:
        if not isinstance(query, dict) or set(query) != {
            "id",
            "text",
            "language",
            "scope",
        }:
            raise ValueError("query fields must be id, text, language, scope only")
        if not isinstance(query["id"], str) or not query["id"] or query["id"] in seen:
            raise ValueError("query IDs must be unique nonempty strings")
        seen.add(query["id"])
        if not isinstance(query["text"], str) or not query["text"].strip():
            raise ValueError("query text must be nonempty")
        if query["language"] not in {"en", "ja"}:
            raise ValueError("unsupported query language")
        scope = query["scope"]
        if scope == {"kind": "global"}:
            continue
        if (
            not isinstance(scope, dict)
            or set(scope) != {"kind", "video_id"}
            or scope["kind"] != "video"
            or scope["video_id"] not in videos
        ):
            raise ValueError("scope must be global or an existing video")


def nearest_rank(values: list[float], percentile: float) -> float | None:
    """Nearest-rank percentile: sorted value at ceil(p*n), one based."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def summarize_samples(samples: list[dict]) -> dict:
    groups: dict[tuple[str, str], list[dict]] = {}
    for sample in samples:
        groups.setdefault((sample["phase"], sample["mode"]), []).append(sample)
    summary = {}
    for (phase, mode), rows in sorted(groups.items()):
        successful = [row["duration_ms"] for row in rows if row["status"] == "ok"]
        summary[f"{phase}:{mode}"] = {
            "attempted": len(rows),
            "successful": len(successful),
            "failed_or_degraded": len(rows) - len(successful),
            "p50_ms": nearest_rank(successful, 0.50),
            "p95_ms": nearest_rank(successful, 0.95),
        }
    return summary


def _rank_signature(results: list[dict]) -> list[dict]:
    return [
        {
            "video_id": row["video_id"],
            "chunk_index": int(row["chunk_index"]),
            "start": float(row["start"]),
            "end": float(row["end"]),
            "score": float(row["score"]),
            "evidence_sha256": hashlib.sha256(row["text"].encode("utf-8")).hexdigest(),
        }
        for row in results
    ]


def _diagnostics_failed(details: dict, mode: str) -> bool:
    """Require affirmative completion of every requested production stage."""
    if not isinstance(details, dict) or details.get("status") not in {
        "ok",
        "no_matches",
    }:
        return True
    stages = details.get("stages")
    if not isinstance(stages, dict):
        return True
    for name in ("dense", "lexical"):
        stage = stages.get(name)
        allowed = {"ok", "no_matches"} if mode in {name, "hybrid"} else {"skipped"}
        if (
            not isinstance(stage, dict)
            or stage.get("status") not in allowed
            or stage.get("error_type")
        ):
            return True
    return bool((details.get("reranker") or {}).get("error"))


def measure_requests(
    service, workload: dict, config: dict = CONFIG
) -> tuple[list[dict], dict]:
    """Run fixed order once, then identical ordered warm repetitions."""
    samples = []
    signatures: dict[str, list[list[dict]]] = {}
    for repetition in range(config["warm_repetitions"] + 1):
        phase = "first_use" if repetition == 0 else "warm"
        for query in workload["queries"]:
            for mode in config["modes"]:
                key = f"{query['id']}:{mode}"
                started = time.perf_counter()
                try:
                    outcome = service.retrieve(
                        query["text"],
                        k=config["k"],
                        language=query["language"],
                        retrieval_mode=mode,
                        video_id=query["scope"].get("video_id"),
                        retrieval_profile=config["retrieval_profile"],
                        reranker=config["reranker"],
                        feedback_tuning=False,
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                    details = outcome["details"]
                    stages = details.get("stages") or {}
                    degraded = _diagnostics_failed(details, mode)
                    status = "failed_or_degraded" if degraded else "ok"
                    ranks = _rank_signature(outcome["results"])
                    if status == "ok":
                        signatures.setdefault(key, []).append(ranks)
                    sample = {
                        "query_id": query["id"],
                        "mode": mode,
                        "phase": phase,
                        "repetition": repetition,
                        "duration_ms": elapsed,
                        "status": status,
                        "result_count": len(ranks),
                        "stages": stages,
                        "fallback": details.get("fallback"),
                        "ranks": ranks,
                    }
                except Exception as exc:
                    sample = {
                        "query_id": query["id"],
                        "mode": mode,
                        "phase": phase,
                        "repetition": repetition,
                        "duration_ms": (time.perf_counter() - started) * 1000,
                        "status": "failed_or_degraded",
                        "error_type": type(exc).__name__,
                        "result_count": 0,
                        "stages": {},
                        "fallback": None,
                        "ranks": [],
                    }
                samples.append(sample)
    expected = config["warm_repetitions"] + 1
    parity = {
        key: len(variants) == expected
        and all(item == variants[0] for item in variants[1:])
        for key, variants in signatures.items()
    }
    for query in workload["queries"]:
        for mode in config["modes"]:
            parity.setdefault(f"{query['id']}:{mode}", False)
    return samples, parity


def install_offline_guard() -> None:
    """Block sockets and dotenv before importing production retrieval code."""
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
            "PYTHON_DOTENV_DISABLED": "1",
            "YT_RAG_SKIP_GLOBAL_SERVICE": "1",
            "YT_RAG_FEEDBACK_TUNING": "0",
            "YT_RAG_FORCE_HASH_EMBEDDINGS": "0",
            "YT_RAG_RERANKER": "none",
        }
    )
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "SAKANA_API_KEY",
        "FUGU_API_KEY",
        "YOUTUBE_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "HF_TOKEN",
        "HUGGINGFACE_HUB_TOKEN",
    ):
        os.environ.pop(key, None)

    def blocked(*_args, **_kwargs):
        raise RuntimeError("network access forbidden during offline performance run")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *_args, **_kwargs: False
    sys.modules["dotenv"] = dotenv_stub


def _source_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()


def _code_provenance() -> dict:
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=REPO_ROOT
    )
    diff = subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=REPO_ROOT)
    paths = subprocess.check_output(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "*.py",
            "Makefile",
        ],
        cwd=REPO_ROOT,
    )
    source_digest = hashlib.sha256()
    for relative in sorted(set(paths.split(b"\0")) - {b""}):
        path = REPO_ROOT / os.fsdecode(relative)
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            source_digest.update(relative + b"\0" + digest.encode() + b"\0")
    return {
        "source_commit": _source_commit(),
        "worktree_clean": not bool(status.strip()),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "source_tree_sha256": source_digest.hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def _model_snapshot_hash(snapshot: Path) -> str:
    """Hash cached model bytes, including files reached through cache symlinks."""
    entries = {}
    for path in sorted(snapshot.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        entries[str(path.relative_to(snapshot))] = digest.hexdigest()
    if not entries:
        raise ValueError("cached model snapshot contains no files")
    return canonical_hash(entries)


def _machine_metadata(processor) -> dict:
    versions = {}
    for package in ("sentence-transformers", "faiss-cpu", "numpy", "torch"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "model_device": str(processor.embed_model.device),
        "packages": versions,
    }


def _prepare_output(path: Path, forbidden: list[Path]) -> None:
    resolved = path.resolve()
    if any(resolved == root or root in resolved.parents for root in forbidden):
        raise ValueError("output must be outside the repository and input trees")
    path.mkdir(mode=0o700, parents=False, exist_ok=False)
    path.chmod(0o700)


def _write_private_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    path.chmod(0o400)


def validate_model_snapshot_path(
    model_path: Path, expected_revision: str | None
) -> str:
    """Require the cached E5-large owner, name, and pinned snapshot revision."""
    revision = model_path.name
    if (
        model_path.parent.name != "snapshots"
        or model_path.parent.parent.name != "models--intfloat--multilingual-e5-large"
        or len(revision) != 40
        or any(char not in "0123456789abcdef" for char in revision)
    ):
        raise ValueError(
            "model path must be a pinned intfloat/multilingual-e5-large snapshot"
        )
    if expected_revision and revision != expected_revision:
        raise ValueError("model revision does not match snapshot path")
    if not (model_path / "modules.json").is_file():
        raise ValueError("cached SentenceTransformer snapshot is incomplete")
    return revision


def run(args: argparse.Namespace) -> dict:
    snapshot = args.snapshot_data.resolve(strict=True)
    source = args.source_data.resolve(strict=True) if args.source_data else None
    model_path = args.model_path.resolve(strict=True)
    revision = validate_model_snapshot_path(model_path, args.model_revision)
    original_hashes = record_hashes(source) if source else None
    snapshot_hashes = record_hashes(snapshot)
    original_manifest_hash = _manifest_hash(source) if source else None
    snapshot_manifest_hash = _manifest_hash(snapshot)
    if original_hashes is not None and (
        original_hashes != snapshot_hashes
        or original_manifest_hash != snapshot_manifest_hash
    ):
        raise ValueError("snapshot records differ from source records")

    code_provenance = _code_provenance()
    output = args.output.resolve()
    _prepare_output(
        output, [REPO_ROOT, snapshot, model_path] + ([source] if source else [])
    )
    setup_ms = {}
    install_offline_guard()
    os.environ["YT_RAG_EMBED_MODEL"] = "intfloat/multilingual-e5-large"
    os.environ["YT_RAG_EMBED_REVISION"] = revision
    os.environ["YT_RAG_EMBED_LOCAL_PATH"] = str(model_path)
    try:
        started = time.perf_counter()
        model_hash = _model_snapshot_hash(model_path)
        setup_ms["model_snapshot_hash"] = (time.perf_counter() - started) * 1000
        with tempfile.TemporaryDirectory(prefix="retrieval-performance-") as temp:
            temp_data = Path(temp) / "data"
            started = time.perf_counter()
            copied_hashes = copy_records(snapshot, temp_data)
            videos = load_records(temp_data)
            setup_ms["copy_and_parse_records"] = (time.perf_counter() - started) * 1000

            workload = (
                json.loads(args.workload.read_text(encoding="utf-8"))
                if args.workload
                else generated_workload(videos)
            )
            validate_workload(workload, videos)
            _write_private_json(output / "workload.json", workload)

            started = time.perf_counter()
            preview_path = str(REPO_ROOT / "local_preview")
            if preview_path not in sys.path:
                sys.path.insert(0, preview_path)
            from multilingual.text_processing import TextProcessor
            from multilingual.video_library import VideoLibrary
            from multilingual.rag_engine import RAGEngine
            from local_preview.local_api import (
                LEXICAL_TOKENIZER_MORPH_VERSION,
                LocalRAGService,
                _ja_lexical_tokenizer,
                lexical_token_version,
            )
            import faiss

            setup_ms["imports"] = (time.perf_counter() - started) * 1000

            started = time.perf_counter()
            processor = TextProcessor()
            metadata = processor.embedding_metadata()
            if (
                metadata.get("backend") != "sentence_transformers"
                or metadata.get("model") != "intfloat/multilingual-e5-large"
                or metadata.get("revision") != revision
                or processor.embed_model is None
            ):
                raise RuntimeError(
                    "pinned real E5 backend unavailable; hashing fallback rejected"
                )
            if not {"en", "ja"}.issubset(processor.tokenizers):
                raise RuntimeError("required language tokenizer unavailable")
            if (
                _ja_lexical_tokenizer() is None
                or lexical_token_version() != LEXICAL_TOKENIZER_MORPH_VERSION
            ):
                raise RuntimeError("Japanese lexical tokenizer unavailable")
            setup_ms["model_and_tokenizers"] = (time.perf_counter() - started) * 1000

            started = time.perf_counter()
            # The copied manifest would trigger VideoLibrary's auto-load and
            # rebuild. Use a separate empty runtime directory to build once.
            library = VideoLibrary(
                data_dir=str(Path(temp) / "runtime"), processor=processor
            )
            library.videos = videos
            library._rebuild_index()
            chunk_count = sum(len(v["chunks"]) for v in videos.values())
            if (
                library.index is None
                or int(library.index.ntotal) != chunk_count
                or len(library.chunk_map) != chunk_count
                or int(library.index.d) != int(metadata["dim"])
                or library.index_embedding_metadata != metadata
                or library._preserve_saved_index
            ):
                raise RuntimeError("rebuilt index identity or count invalid")
            index_hash = hashlib.sha256(
                bytes(faiss.serialize_index(library.index))
            ).hexdigest()
            setup_ms["index_rebuild"] = (time.perf_counter() - started) * 1000

            service = LocalRAGService.__new__(LocalRAGService)
            service.engine = RAGEngine(library=library)
            service.feedback_tuning_enabled = False
            service.feedback = {}
            service.feedback_index = {}
            samples, parity = measure_requests(service, workload)

            if (
                record_hashes(snapshot) != snapshot_hashes
                or _manifest_hash(snapshot) != snapshot_manifest_hash
            ):
                raise RuntimeError("snapshot records changed during execution")
            if source and (
                record_hashes(source) != original_hashes
                or _manifest_hash(source) != original_manifest_hash
            ):
                raise RuntimeError("source records changed during execution")
            if _model_snapshot_hash(model_path) != model_hash:
                raise RuntimeError("cached model bytes changed during execution")
            if _code_provenance() != code_provenance:
                raise RuntimeError("source checkout or runner changed during execution")
            report = {
                "kind": "ungraded_retrieval_performance",
                "quality": "not_evaluated",
                "created_at": datetime.now(timezone.utc).isoformat(),
                **code_provenance,
                "corpus_sha256": canonical_hash(
                    {"manifest": snapshot_manifest_hash, "records": copied_hashes}
                ),
                "manifest_sha256": snapshot_manifest_hash,
                "workload_sha256": canonical_hash(workload),
                "config_sha256": canonical_hash(CONFIG),
                "index_sha256": index_hash,
                "model_snapshot_sha256": model_hash,
                "index_origin": "rebuilt_from_snapshot_records_in_temporary_storage",
                "embedding": metadata,
                "lexical_token_version": lexical_token_version(),
                "machine": _machine_metadata(processor),
                "corpus": {
                    "videos": len(videos),
                    "chunks": int(library.index.ntotal),
                    "languages": dict(Counter(v["language"] for v in videos.values())),
                },
                "workload_origin": "bounded_midpoint_chunk_probes"
                if not args.workload
                else "private_file",
                "workload_query_count": len(workload["queries"]),
                "config": CONFIG,
                "cache_policy": "one process; one model and rebuilt index; service lexical cache initially empty; no model, OS, or disk cache reset between requests",
                "percentile_policy": "nearest_rank: sorted successful samples at ceil(p*n), p50=.50, p95=.95",
                "setup_ms": setup_ms,
                "summary": summarize_samples(samples),
                "parity_all": all(parity.values()),
                "parity_by_query_mode": parity,
                "failed_or_degraded_count": sum(s["status"] != "ok" for s in samples),
                "samples": samples,
            }
            _write_private_json(output / "report.json", report)
            return report
    except Exception as exc:
        _write_private_json(
            output / "failure.json",
            {
                "kind": "ungraded_retrieval_performance",
                "quality": "not_evaluated",
                "status": "setup_or_execution_failed",
                "error_type": type(exc).__name__,
                "code": code_provenance,
                "setup_ms": setup_ms,
            },
        )
        raise
    finally:
        output.chmod(0o500)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-data", type=Path, required=True)
    parser.add_argument(
        "--source-data",
        type=Path,
        help="read-only original for before/after record verification",
    )
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument(
        "--model-revision", help="expected 40-character snapshot revision"
    )
    parser.add_argument(
        "--workload",
        type=Path,
        help="private ungraded workload JSON; default frozen source probes",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new private directory outside repo and inputs",
    )
    args = parser.parse_args()
    try:
        report = run(args)
    except Exception as exc:
        print(f"performance run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "kind": report["kind"],
                "quality": report["quality"],
                "corpus": report["corpus"],
                "failed_or_degraded_count": report["failed_or_degraded_count"],
                "parity_all": report["parity_all"],
                "summary": report["summary"],
            },
            indent=2,
        )
    )
    return 1 if report["failed_or_degraded_count"] or not report["parity_all"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
