"""Run private, offline retrieval evaluations against the actual application stack.

No provider calls, acquisition, preexisting indexes, fixture scorers, or model
fetches are used. Inputs and per-query reports remain local and untracked.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
from functools import lru_cache
import json
from importlib import metadata as package_metadata
import math
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.contracts import (  # noqa: E402
    ContractError,
    file_sha256,
    load_config,
    load_corpus,
    load_dataset,
    load_manifest,
    require_private,
)
from evals.statistics import bootstrap_mean_ci, score_ranking  # noqa: E402


def git_provenance(root: Path = ROOT) -> dict:
    """Record source identity without exposing private filenames or diff contents."""

    def git(*args):
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, check=False
        )
        if result.returncode:
            raise ContractError("git provenance unavailable; run inside a Git checkout")
        return result.stdout

    status = git("status", "--porcelain=v1", "--untracked-files=all")
    source_digest = hashlib.sha256()
    paths = git(
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
        "*.py",
        "Makefile",
    )
    for relative in sorted(set(paths.split(b"\0")) - {b""}):
        path = root / os.fsdecode(relative)
        if path.is_file():
            source_digest.update(relative + b"\0" + file_sha256(path).encode() + b"\0")
    return {
        "commit": git("rev-parse", "HEAD").decode().strip(),
        "dirty": bool(status),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(
            git("diff", "HEAD", "--binary")
        ).hexdigest(),
        "source_tree_sha256": source_digest.hexdigest(),
    }


def resolve_snapshot(model: str, revision: str, local_path: str | None = None) -> Path:
    """Use the exact cached Hugging Face snapshot; never resolve a moving ref."""
    model_directory = "models--" + model.replace("/", "--")
    if local_path:
        snapshot = Path(local_path).expanduser().resolve()
        if (
            snapshot.name != revision
            or snapshot.parent.name != "snapshots"
            or snapshot.parent.parent.name != model_directory
        ):
            raise ContractError(
                "local_path must point to the model's exact Hugging Face snapshots/<revision> directory"
            )
    else:
        cache = Path(
            os.environ.get("HF_HUB_CACHE")
            or os.environ.get("HUGGINGFACE_HUB_CACHE")
            or (
                Path(
                    os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface"
                )
                / "hub"
            )
        )
        snapshot = cache.expanduser() / model_directory / "snapshots" / revision
    if not snapshot.is_dir():
        raise ContractError(
            f"Pinned model is not cached locally: {model}@{revision}; downloads are disabled"
        )
    filenames = {path.name for path in snapshot.rglob("*") if path.is_file()}
    if "config.json" not in filenames or not any(
        name.endswith((".safetensors", ".bin")) for name in filenames
    ):
        raise ContractError(f"Pinned model snapshot is incomplete: {model}@{revision}")
    return snapshot


@contextmanager
def offline_runtime(embedding: dict, snapshot: Path):
    """Isolate model settings and disable dotenv reads and outbound connections."""
    settings = {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "YT_RAG_SKIP_GLOBAL_SERVICE": "1",
        "YT_RAG_FORCE_HASH_EMBEDDINGS": "0",
        "YT_RAG_EMBED_MODEL": embedding["model"],
        "YT_RAG_EMBED_REVISION": embedding["revision"],
        "YT_RAG_EMBED_LOCAL_PATH": str(snapshot),
        "YT_RAG_RERANKER": "none",
        "YT_RAG_FEEDBACK_TUNING": "0",
    }
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, settings))
        try:
            import dotenv
        except ImportError:
            pass
        else:
            stack.enter_context(patch.object(dotenv, "load_dotenv", return_value=False))
        stack.enter_context(
            patch(
                "socket.socket.connect",
                side_effect=RuntimeError("evaluation network access disabled"),
            )
        )
        stack.enter_context(
            patch(
                "socket.socket.connect_ex",
                side_effect=RuntimeError("evaluation network access disabled"),
            )
        )
        yield


def build_service(corpus: dict, data_dir: Path, embedding: dict, config: dict):
    """Compose actual application classes around an ephemeral raw-text library."""
    preview = ROOT / "local_preview"
    if str(preview) not in sys.path:
        sys.path.insert(0, str(preview))
    from local_preview.local_api import LocalRAGService
    from multilingual.rag_engine import RAGEngine
    from multilingual.reranker import CrossEncoderReranker
    from multilingual.text_processing import TextProcessor
    from multilingual.video_library import VideoLibrary

    processor = TextProcessor()
    metadata = processor.embedding_metadata()
    if (
        metadata.get("backend") != "sentence_transformers"
        or processor.embed_model is None
    ):
        raise ContractError(
            "Real E5 model unavailable: hashing fallback is forbidden for real evaluation"
        )
    if metadata.get("model") != embedding["model"]:
        raise ContractError("Active embedding model does not match manifest")
    required_languages = {video["language"] for video in corpus["videos"]}
    if required_languages - set(processor.tokenizers):
        raise ContractError("A required application language tokenizer is unavailable")
    library = VideoLibrary(data_dir=str(data_dir), processor=processor)
    for video in corpus["videos"]:
        chunks = [
            dict(
                chunk,
                embed_text=processor.make_embed_text(
                    chunk["raw_text"], video["language"]
                ),
            )
            for chunk in video["chunks"]
        ]
        library.videos[video["video_id"]] = {
            "title": video["title"],
            "language": video["language"],
            "chunks": chunks,
            "url": f"https://www.youtube.com/watch?v={video['video_id']}",
            "summary_cache": {},
            "chunking": library.current_chunking_metadata(),
        }
    library._rebuild_index()
    verify_index(library, metadata)
    # Bypass the application's runtime-data initialization only. All retrieval,
    # fusion, reranking and diversity methods remain the production methods.
    service = LocalRAGService.__new__(LocalRAGService)
    service.engine = RAGEngine(library=library)
    service.lock = threading.Lock()
    service.feedback_lock = threading.Lock()
    service.feedback_tuning_enabled = False
    service.feedback = {}
    service.feedback_index = {}
    reranker_metadata = None
    if any(run["reranker"] == "cross_encoder" for run in config["runs"]):
        spec = config["reranker"]
        snapshot = resolve_snapshot(
            spec["model"], spec["revision"], spec.get("local_path")
        )
        service._reranker = CrossEncoderReranker(model_name=str(snapshot))
        if service._reranker._ensure_score_fn() is None:
            raise ContractError(
                "Requested cross-encoder is unavailable; reranking fallback is forbidden"
            )
        reranker_metadata = {
            "model": spec["model"],
            "revision": spec["revision"],
            "snapshot": str(snapshot),
        }
    return service, dict(metadata, revision=embedding["revision"]), reranker_metadata


def verify_index(library, expected: dict) -> None:
    """Fail instead of scoring an index built in another embedding space."""
    if library.index_embedding_metadata != expected or getattr(
        library, "_preserve_saved_index", False
    ):
        raise ContractError(
            "Index embedding provenance does not match the active E5 processor"
        )
    count = sum(len(video["chunks"]) for video in library.videos.values())
    if (
        library.index is None
        or library.index.ntotal != count
        or len(library.chunk_map) != count
    ):
        raise ContractError("Index/chunk-map cardinality mismatch")
    if library.index.d != expected["dim"]:
        raise ContractError("Index embedding dimension mismatch")


def _latencies(values: list[float]) -> dict:
    if not values:
        return {"p50": None, "p95": None, "samples": 0, "p95_method": "nearest_rank"}
    ordered = sorted(values)
    return {
        "p50": median(ordered),
        "p95": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
        "samples": len(ordered),
        "p95_method": "nearest_rank",
    }


@lru_cache(maxsize=1024)
def _mean_ci(values: tuple) -> dict:
    return bootstrap_mean_ci(list(values), seed=0, samples=10000)


def _stage_summary(rows: list[dict]) -> dict:
    summary = {}
    for name in ("dense", "lexical"):
        values, statuses = [], {}
        for row in rows:
            for sample in row["stage_timings_ms"]:
                stage = sample[name]
                status = stage["status"]
                statuses[status] = statuses.get(status, 0) + 1
                duration = stage["duration_ms"]
                if (
                    status != "skipped"
                    and isinstance(duration, (int, float))
                    and math.isfinite(duration)
                ):
                    values.append(duration)
        summary[name] = dict(
            _latencies(values),
            statuses=statuses,
            unavailable_samples=statuses.get("not_reported", 0),
            skipped_samples=statuses.get("skipped", 0),
        )
    return summary


def _summary(rows: list[dict]) -> dict:
    metrics = {}
    intervals = {}
    names = {name for row in rows for name in row["metrics"]}
    for name in sorted(names):
        values = [
            row["metrics"][name] for row in rows if row["metrics"][name] is not None
        ]
        metrics[name] = sum(values) / len(values) if values else None
        intervals[name] = _mean_ci(tuple(values)) if values else None
    return {
        "query_count": len(rows),
        "answerable_query_count": sum(not row["unanswerable"] for row in rows),
        "unanswerable_query_count": sum(row["unanswerable"] for row in rows),
        "failed_query_count": sum(row["error"] is not None for row in rows),
        "metrics": metrics,
        "metrics_ci95": intervals,
        "latency_ms": _latencies(
            [value for row in rows for value in row["latency_ms"]]
        ),
        "stage_latency_ms": _stage_summary(rows),
    }


def run_queries(service, dataset: list[dict], run: dict, config: dict) -> dict:
    """Score every case, retaining failed answerable queries in denominators."""
    rows = []
    for query in dataset:
        ranked_ids, latencies, error = [], [], None
        stage_timings = []
        details = {}
        try:
            for repetition in range(config["latency_repetitions"]):
                stage_timings.append(
                    {
                        name: {"status": "not_reported", "duration_ms": None}
                        for name in ("dense", "lexical")
                    }
                )
                started = time.perf_counter()
                try:
                    response = service.retrieve(
                        query["query"],
                        k=config["top_k"],
                        language=query["language"],
                        retrieval_mode=run["retrieval_mode"],
                        retrieval_profile=run.get("retrieval_profile"),
                        reranker=run["reranker"],
                        video_ids=query.get("video_ids"),
                    )
                finally:
                    latencies.append((time.perf_counter() - started) * 1000)
                details = response.get("details") or {}
                for name in ("dense", "lexical"):
                    stage = details.get("stages", {}).get(name, {})
                    stage_timings[-1][name] = {
                        "status": stage.get("status", "not_reported"),
                        "duration_ms": stage.get("duration_ms"),
                    }
                if details.get("status") in {"error", "degraded"} or any(
                    stage.get("status") == "error"
                    for stage in details.get("stages", {}).values()
                ):
                    raise ContractError(
                        "application retrieval stage failed or degraded"
                    )
                results = response["results"]
                reranker = details.get("reranker") or {}
                if run["reranker"] == "cross_encoder" and (
                    reranker.get("error") or (results and not reranker.get("applied"))
                ):
                    raise ContractError(
                        "requested cross-encoder was not applied successfully"
                    )
                current_ids = [
                    f"{row['video_id']}:{int(row['chunk_index'])}" for row in results
                ]
                if repetition and current_ids != ranked_ids:
                    raise ContractError(
                        "retrieval ranking changed across latency repetitions"
                    )
                ranked_ids = current_ids
        except Exception as exc:
            error = str(exc) if isinstance(exc, ContractError) else type(exc).__name__
        metrics = score_ranking(
            query["judgments"],
            [] if error else ranked_ids,
            unanswerable=query["unanswerable"],
        )
        if metrics is None:
            metrics = {
                name: None for name in ("recall@5", "recall@10", "mrr@10", "ndcg@10")
            }
        rows.append(
            {
                **{
                    key: query[key]
                    for key in (
                        "id",
                        "group",
                        "language",
                        "query_type",
                        "difficulty",
                        "unanswerable",
                    )
                },
                "metrics": metrics,
                "ranked_ids": ranked_ids,
                "error": error,
                "latency_ms": latencies,
                "stage_timings_ms": stage_timings,
                "judged_fraction": sum(
                    chunk in query["judgments"] for chunk in ranked_ids
                )
                / len(ranked_ids)
                if ranked_ids
                else None,
                "retrieval_status": details.get("status"),
                "unanswerable_returned_results": bool(ranked_ids)
                if query["unanswerable"]
                else None,
            }
        )
    groups = {}
    for field in ("group", "language", "query_type", "difficulty"):
        groups[field] = {
            value: _summary([row for row in rows if row[field] == value])
            for value in sorted({row[field] for row in rows})
        }
    return {
        "name": run["name"],
        "config": run,
        **_summary(rows),
        "groups": groups,
        "per_query": rows,
    }


@contextmanager
def _reserve_output(out_dir: Path):
    """Reserve an unused report path before model work; reject concurrent writers."""
    require_private(out_dir / "results.json")
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = out_dir / ".real-eval.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ContractError(
            "Output directory is reserved by another run; choose a new --out directory"
        ) from exc
    os.close(descriptor)
    try:
        if (out_dir / "results.json").exists():
            raise ContractError(
                "Refusing to overwrite an existing report; choose a new --out directory"
            )
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _write_result(out_dir: Path, payload: dict) -> Path:
    path = out_dir / "results.json"
    require_private(path)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=out_dir, prefix=".result-", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            json.dump(payload, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            # Hard-link creation is atomic and refuses an existing destination.
            os.link(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
    return path


def run_benchmark(
    manifest_path: Path,
    config_path: Path,
    out_dir: Path,
    *,
    split="dev",
    allow_holdout=False,
) -> dict:
    """Reserve an immutable artifact before invoking any model or retrieval code."""
    with _reserve_output(out_dir):
        return _run_benchmark(
            manifest_path,
            config_path,
            out_dir,
            split=split,
            allow_holdout=allow_holdout,
        )


def _run_benchmark(
    manifest_path: Path,
    config_path: Path,
    out_dir: Path,
    *,
    split="dev",
    allow_holdout=False,
) -> dict:
    """Run a real-stack pilot; a completed report is evidence, never a promotion."""
    require_private(out_dir / "results.json")
    result = {
        "schema_version": 1,
        "kind": "real_retrieval",
        "status": "failed",
        "split": split,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs": [],
        "errors": [],
        "limitations": [
            "Retrieval only: no generated-answer, abstention, acquisition or provider evaluation.",
            "Recall and nDCG are relative to supplied judgments; unjudged chunks count as nonrelevant.",
            "Unanswerable cases have null ranking metrics; returned results do not measure answer abstention.",
            "Latency includes application retrieval, excludes model loading/index building, and uses a shared warm process.",
            "No promotion decision is produced by this pilot runner.",
        ],
        "provenance": {
            "network": "disabled",
            "paid_providers": "disabled",
            "index": "rebuilt_from_manifest_in_temporary_directory",
        },
    }
    try:
        verified = load_manifest(
            manifest_path, split=split, allow_holdout=allow_holdout
        )
        config = load_config(config_path)
        corpus, chunk_ids = load_corpus(verified["corpus_path"])
        dataset = load_dataset(
            verified["dataset_path"], split=split, chunk_ids=chunk_ids
        )
        # Reading holdout never occurs on dev runs. On holdout runs, reject exact
        # query/ID leakage against the hashed dev split before model startup.
        if split == "holdout":
            dev_verified = load_manifest(manifest_path, split="dev")
            dev = load_dataset(
                dev_verified["dataset_path"], split="dev", chunk_ids=chunk_ids
            )
            if {row["id"] for row in dataset} & {row["id"] for row in dev} or {
                " ".join(row["query"].casefold().split()) for row in dataset
            } & {" ".join(row["query"].casefold().split()) for row in dev}:
                raise ContractError(
                    "dev and holdout must have distinct query IDs and query text"
                )
        provenance = result["provenance"]
        provenance.update(
            {
                key: verified[key]
                for key in ("manifest_sha256", "corpus_sha256", "dataset_sha256")
            }
        )
        provenance.update(
            git=git_provenance(),
            config_sha256=file_sha256(config_path),
            effective_config=config,
            scoring_version="graded_v1",
            bootstrap={
                "seed": 0,
                "samples": 10000,
                "unit": "query",
                "confidence": 0.95,
            },
            evidence_level=verified["manifest"]["evidence_level"],
            label_review="user_asserted_not_independently_attested",
            timing_policy={
                "clock": "perf_counter",
                "scope": "application_retrieval",
                "setup_excluded": True,
                "repetitions": config["latency_repetitions"],
                "warmup": "none",
                "process": "shared_across_queries_and_runs",
                "p95": "nearest_rank",
            },
        )
        embedding = verified["manifest"]["embedding"]
        provenance["embedding"] = dict(embedding, backend="not_loaded")
        snapshot = resolve_snapshot(
            embedding["model"], embedding["revision"], embedding.get("local_path")
        )
        with (
            offline_runtime(embedding, snapshot),
            tempfile.TemporaryDirectory(prefix="youtube-real-eval-") as directory,
        ):
            started = time.perf_counter()
            service, metadata, reranker = build_service(
                corpus, Path(directory), embedding, config
            )
            provenance["embedding"] = dict(metadata, snapshot=str(snapshot))
            provenance["reranker"] = reranker
            provenance["packages"] = {}
            for distribution in (
                "numpy",
                "faiss-cpu",
                "sentence-transformers",
                "transformers",
                "fugashi",
            ):
                try:
                    provenance["packages"][distribution] = package_metadata.version(
                        distribution
                    )
                except package_metadata.PackageNotFoundError:
                    provenance["packages"][distribution] = None
            import faiss

            index = service.engine.library.index
            serialized = faiss.serialize_index(index)
            provenance["index"] = {
                "type": type(index).__name__,
                "ntotal": index.ntotal,
                "dim": index.d,
                "serialized_bytes": serialized.nbytes,
                "sha256": hashlib.sha256(serialized.tobytes()).hexdigest(),
            }
            provenance["setup_duration_ms"] = (time.perf_counter() - started) * 1000
            for run in config["runs"]:
                verify_index(
                    service.engine.library,
                    service.engine.library.processor.embedding_metadata(),
                )
                result["runs"].append(run_queries(service, dataset, run, config))
        for field, path in (
            ("manifest_sha256", manifest_path),
            ("corpus_sha256", verified["corpus_path"]),
            ("dataset_sha256", verified["dataset_path"]),
            ("config_sha256", config_path),
        ):
            if file_sha256(path) != provenance[field]:
                raise ContractError(
                    "Evaluation input changed during execution; results are invalid"
                )
        result["status"] = (
            "failed"
            if any(run["failed_query_count"] for run in result["runs"])
            else "completed"
        )
    except Exception as exc:
        result["errors"].append(
            str(exc) if isinstance(exc, ContractError) else type(exc).__name__
        )
    _write_result(out_dir, result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--allow-holdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_benchmark(
            args.manifest,
            args.config,
            args.out,
            split=args.split,
            allow_holdout=args.allow_holdout,
        )
    except (ContractError, OSError) as exc:
        print(
            f"Evaluation could not write its local report: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2
    print(
        f"Real retrieval evaluation: {result['status']}; report: {args.out / 'results.json'}"
    )
    for error in result["errors"]:
        print(error, file=sys.stderr)
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
