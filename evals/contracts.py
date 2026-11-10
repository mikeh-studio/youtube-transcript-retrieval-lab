"""Strict, dependency-free contracts for private real retrieval evaluations."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
E5_MODELS = {"intfloat/multilingual-e5-base", "intfloat/multilingual-e5-large"}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")


class ContractError(ValueError):
    """An input cannot support a reproducible real evaluation."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(
            f"Cannot read JSON input {path.name}: {type(exc).__name__}"
        ) from exc


def require_private(path: Path, root: Path = ROOT) -> None:
    """Refuse tracked private inputs or reports; outside-repository inputs are fine."""
    path = path.resolve()
    try:
        relative = path.relative_to(root.resolve())
    except ValueError:
        return
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(relative)],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        raise ContractError(
            f"Private evaluation input/output must not be tracked: {relative}"
        )
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", str(relative)],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if ignored.returncode != 0:
        raise ContractError(f"Private evaluation path must be gitignored: {relative}")


def _text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a nonempty string")
    return value


def _hashed_file(spec: dict, base: Path, name: str) -> tuple[Path, str]:
    if not isinstance(spec, dict):
        raise ContractError(f"{name} must specify path and sha256")
    path = (base / _text(spec.get("path"), f"{name}.path")).resolve()
    expected = spec.get("sha256")
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise ContractError(f"{name}.sha256 must be a lowercase SHA-256 digest")
    require_private(path)
    try:
        actual = file_sha256(path)
    except OSError as exc:
        raise ContractError(f"{name} file unavailable: {type(exc).__name__}") from exc
    if actual != expected:
        raise ContractError(
            f"{name} SHA-256 mismatch; update the reviewed manifest explicitly"
        )
    return path, actual


def load_manifest(
    path: Path, *, split: str = "dev", allow_holdout: bool = False
) -> dict:
    """Resolve and verify the selected split before any model is loaded."""
    if split not in {"dev", "holdout"}:
        raise ContractError("split must be dev or holdout")
    if split == "holdout" and not allow_holdout:
        raise ContractError("holdout requires --allow-holdout as an explicit opt-in")
    path = path.resolve()
    require_private(path)
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ContractError("manifest schema_version must be 1")
    if payload.get("evidence_level") not in {
        "real_component_smoke",
        "user_asserted_reviewed",
    }:
        raise ContractError(
            "manifest.evidence_level must be real_component_smoke or user_asserted_reviewed; review is user asserted"
        )
    embedding = payload.get("embedding")
    if not isinstance(embedding, dict) or embedding.get("model") not in E5_MODELS:
        raise ContractError(
            "embedding.model must identify multilingual-e5-base or multilingual-e5-large"
        )
    if not REVISION.fullmatch(str(embedding.get("revision", ""))):
        raise ContractError(
            "embedding.revision must be an immutable 40-character commit SHA"
        )
    datasets = payload.get("datasets")
    if not isinstance(datasets, dict) or "dev" not in datasets or split not in datasets:
        raise ContractError("manifest.datasets must include dev and the selected split")
    corpus_path, corpus_hash = _hashed_file(
        payload.get("corpus"), path.parent, "corpus"
    )
    dataset_path, dataset_hash = _hashed_file(
        datasets[split], path.parent, f"datasets.{split}"
    )
    return {
        "manifest": payload,
        "manifest_path": path,
        "manifest_sha256": file_sha256(path),
        "corpus_path": corpus_path,
        "corpus_sha256": corpus_hash,
        "dataset_path": dataset_path,
        "dataset_sha256": dataset_hash,
        "split": split,
    }


def load_corpus(path: Path) -> tuple[dict, set[str]]:
    """Load raw transcript chunks only; never accept fixture scores or vectors."""
    corpus = read_json(path)
    if not isinstance(corpus, dict) or set(corpus) != {"schema_version", "videos"}:
        raise ContractError("corpus must contain only schema_version and videos")
    if (
        corpus["schema_version"] != 1
        or not isinstance(corpus["videos"], list)
        or not corpus["videos"]
    ):
        raise ContractError(
            "corpus requires schema_version=1 and a nonempty videos list"
        )
    ids, chunk_ids = set(), set()
    for video in corpus["videos"]:
        if not isinstance(video, dict) or set(video) - {
            "video_id",
            "title",
            "language",
            "chunks",
        }:
            raise ContractError(
                "corpus videos support video_id, title, language, chunks only"
            )
        video_id = _text(video.get("video_id"), "video_id")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", video_id) or video_id in ids:
            raise ContractError(
                "video_id must be unique and contain only letters, digits, underscore or dash"
            )
        ids.add(video_id)
        _text(video.get("title"), "title")
        if video.get("language") not in {"en", "ja"}:
            raise ContractError("video language must be en or ja")
        chunks = video.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise ContractError("each video needs nonempty raw transcript chunks")
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict) or set(chunk) != {
                "start",
                "end",
                "raw_text",
            }:
                raise ContractError(
                    "chunks require only start, end, raw_text; fixture scores/vectors are forbidden"
                )
            _text(chunk["raw_text"], "raw_text")
            start, end = chunk["start"], chunk["end"]
            if any(
                type(value) not in {int, float} or not math.isfinite(value)
                for value in (start, end)
            ):
                raise ContractError("chunk timestamps must be finite numbers")
            if start < 0 or end <= start:
                raise ContractError("chunk timestamps require 0 <= start < end")
            chunk_ids.add(f"{video_id}:{index}")
    return corpus, chunk_ids


def load_dataset(path: Path, *, split: str, chunk_ids: set[str]) -> list[dict]:
    """Validate metadata and human-supplied graded evidence judgments."""
    rows, ids = [], set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ContractError(f"dataset line {number} is invalid JSON") from exc
        if not isinstance(row, dict):
            raise ContractError(f"dataset line {number} must be an object")
        for field in (
            "id",
            "query",
            "language",
            "query_type",
            "difficulty",
            "group",
            "judgment_source",
        ):
            _text(row.get(field), f"dataset line {number}.{field}")
        if row["id"] in ids:
            raise ContractError("dataset query IDs must be unique")
        ids.add(row["id"])
        if row.get("split") != split:
            raise ContractError("every dataset row must declare the selected split")
        if (
            row["language"] not in {"en", "ja"}
            or type(row.get("unanswerable")) is not bool
        ):
            raise ContractError(
                "dataset requires en/ja language and boolean unanswerable"
            )
        judgments = row.get("judgments")
        if not isinstance(judgments, dict) or not judgments:
            raise ContractError(
                "judgments must be a nonempty mapping of video_id:chunk_index to grade"
            )
        if any(key not in chunk_ids for key in judgments):
            raise ContractError("judgments reference unknown corpus chunk IDs")
        if any(
            type(grade) is not int or grade not in {0, 1, 2}
            for grade in judgments.values()
        ):
            raise ContractError("judgment grades must be integers 0, 1, or 2")
        positives = any(grade > 0 for grade in judgments.values())
        if positives == row["unanswerable"]:
            raise ContractError(
                "answerable queries need a positive judgment; unanswerable queries must have none"
            )
        scope = row.get("video_ids")
        if scope is not None:
            video_ids = {chunk_id.rsplit(":", 1)[0] for chunk_id in chunk_ids}
            if (
                not isinstance(scope, list)
                or not scope
                or any(item not in video_ids for item in scope)
            ):
                raise ContractError(
                    "video_ids must be a nonempty list of known corpus video IDs"
                )
            if any(
                grade > 0 and key.rsplit(":", 1)[0] not in scope
                for key, grade in judgments.items()
            ):
                raise ContractError(
                    "positive judgments must be within the query video scope"
                )
        rows.append(row)
    if not rows:
        raise ContractError("dataset must contain at least one labeled query")
    return rows


def load_config(path: Path) -> dict:
    """Accept only explicit local retrieval configurations with fixed defaults."""
    config = read_json(path)
    if not isinstance(config, dict) or set(config) - {
        "schema_version",
        "top_k",
        "latency_repetitions",
        "runs",
        "reranker",
    }:
        raise ContractError(
            "config supports schema_version, top_k, latency_repetitions, runs, reranker only"
        )
    if config.get("schema_version") != 1:
        raise ContractError("config schema_version must be 1")
    top_k = config.setdefault("top_k", 10)
    repetitions = config.setdefault("latency_repetitions", 1)
    if type(top_k) is not int or not 10 <= top_k <= 12:
        raise ContractError(
            "top_k must be an integer between 10 and 12 for @10 metrics"
        )
    if type(repetitions) is not int or not 1 <= repetitions <= 20:
        raise ContractError("latency_repetitions must be an integer from 1 to 20")
    runs = config.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ContractError("config needs at least one run")
    names = set()
    for run in runs:
        if not isinstance(run, dict) or set(run) - {
            "name",
            "retrieval_mode",
            "retrieval_profile",
            "reranker",
        }:
            raise ContractError(
                "runs support name, retrieval_mode, retrieval_profile, reranker only"
            )
        name = _text(run.get("name"), "run.name")
        if name in names:
            raise ContractError("run names must be unique")
        names.add(name)
        if run.get("retrieval_mode") not in {"dense", "lexical", "hybrid"}:
            raise ContractError("retrieval_mode must be dense, lexical or hybrid")
        if run.setdefault("reranker", "none") not in {"none", "cross_encoder"}:
            raise ContractError("reranker must be none or cross_encoder")
        if run["retrieval_mode"] == "hybrid":
            if run.setdefault("retrieval_profile", "baseline_rrf") not in {
                "baseline_rrf",
                "optimized_v1",
            }:
                raise ContractError("unknown hybrid retrieval_profile")
        elif run.get("retrieval_profile") is not None:
            raise ContractError("retrieval_profile applies only to hybrid retrieval")
    if any(run["reranker"] == "cross_encoder" for run in runs):
        reranker = config.get("reranker")
        if not isinstance(reranker, dict) or not REVISION.fullmatch(
            str(reranker.get("revision", ""))
        ):
            raise ContractError(
                "cross_encoder requires config.reranker with model and immutable revision"
            )
        _text(reranker.get("model"), "reranker.model")
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Print a local input's SHA-256 without modifying it"
    )
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(file_sha256(args.path))
