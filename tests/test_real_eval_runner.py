"""Synthetic unit tests of failure accounting; these are not benchmark results."""

import json
import socket
from types import SimpleNamespace

import pytest

from evals.contracts import ContractError
from evals.real_runner import (
    _latencies,
    _reserve_output,
    offline_runtime,
    resolve_snapshot,
    run_benchmark,
    run_queries,
    verify_index,
)
from tests.test_real_eval_contracts import private_inputs  # noqa: F401


def query(query_id="ok", *, unanswerable=False):
    return {
        "id": query_id,
        "query": query_id,
        "language": "en",
        "group": "synthetic",
        "query_type": "factual",
        "difficulty": "easy",
        "unanswerable": unanswerable,
        "judgments": {"synthetic:0": 0 if unanswerable else 2},
    }


class SyntheticService:
    def retrieve(self, query, **kwargs):
        if query == "broken":
            raise RuntimeError("message is deliberately not copied to reports")
        if query == "degraded":
            return {
                "results": [{"video_id": "synthetic", "chunk_index": 0}],
                "details": {"status": "degraded"},
            }
        return {
            "results": [{"video_id": "synthetic", "chunk_index": 0}],
            "details": {"status": "ok", "reranker": {"applied": False}},
        }


def test_mixed_unanswerable_and_failed_queries_keep_denominators():
    run = {"name": "unit", "retrieval_mode": "dense", "reranker": "none"}
    result = run_queries(
        SyntheticService(),
        [query(), query("broken"), query("unanswerable", unanswerable=True)],
        run,
        {"top_k": 10, "latency_repetitions": 1},
    )
    assert result["query_count"] == 3
    assert result["answerable_query_count"] == 2
    assert result["failed_query_count"] == 1
    assert result["metrics"]["recall@5"] == 0.5
    assert result["metrics_ci95"]["recall@5"]["n"] == 2
    assert all(value is None for value in result["per_query"][2]["metrics"].values())
    assert result["per_query"][1]["error"] == "RuntimeError"
    assert result["latency_ms"]["samples"] == 3


def test_degraded_successful_fallback_is_still_failed():
    result = run_queries(
        SyntheticService(),
        [query("degraded")],
        {"name": "unit", "retrieval_mode": "hybrid", "reranker": "none"},
        {"top_k": 10, "latency_repetitions": 1},
    )
    assert result["failed_query_count"] == 1
    assert result["metrics"]["ndcg@10"] == 0


def test_requested_reranker_cannot_silently_fall_back():
    result = run_queries(
        SyntheticService(),
        [query()],
        {"name": "unit", "retrieval_mode": "dense", "reranker": "cross_encoder"},
        {"top_k": 10, "latency_repetitions": 1},
    )
    assert result["failed_query_count"] == 1
    assert result["metrics"]["mrr@10"] == 0
    assert "cross-encoder" in result["per_query"][0]["error"]


def test_snapshot_must_exist_without_download(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    with pytest.raises(ContractError, match="not cached locally"):
        resolve_snapshot("intfloat/multilingual-e5-base", "a" * 40)
    with pytest.raises(ContractError, match="exact Hugging Face"):
        resolve_snapshot("intfloat/multilingual-e5-base", "a" * 40, str(tmp_path))


def test_index_mismatch_fails():
    metadata = {"backend": "sentence_transformers", "model": "e5", "dim": 4}
    library = SimpleNamespace(
        index_embedding_metadata=dict(metadata),
        _preserve_saved_index=False,
        videos={"video": {"chunks": [{}]}},
        chunk_map=[("video", 0)],
        index=SimpleNamespace(ntotal=1, d=4),
    )
    verify_index(library, metadata)
    library.index_embedding_metadata = {"backend": "hashing"}
    with pytest.raises(ContractError, match="provenance"):
        verify_index(library, metadata)
    library.index_embedding_metadata = metadata
    library.index.d = 5
    with pytest.raises(ContractError, match="dimension"):
        verify_index(library, metadata)


def test_offline_runtime_blocks_network_and_restores_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "original")
    with offline_runtime(
        {"model": "intfloat/multilingual-e5-base", "revision": "a" * 40}, tmp_path
    ):
        with (
            socket.socket() as connection,
            pytest.raises(RuntimeError, match="network access disabled"),
        ):
            connection.connect(("127.0.0.1", 443))
        import os

        assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HF_HUB_OFFLINE"] == "original"


def test_model_failure_writes_honest_private_report(
    private_inputs,  # noqa: F811 - pytest fixture import
    tmp_path,
    monkeypatch,
):
    manifest, _, _, _, _ = private_inputs
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [{"name": "dense", "retrieval_mode": "dense"}],
            }
        )
    )
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-cache"))
    result = run_benchmark(manifest, config, tmp_path / "report")
    assert result["status"] == "failed"
    assert result["runs"] == []
    assert "not cached locally" in result["errors"][0]
    assert result["provenance"]["embedding"]["backend"] == "not_loaded"
    report = tmp_path / "report" / "results.json"
    assert report.stat().st_mode & 0o777 == 0o600
    assert result["provenance"]["corpus_sha256"]
    assert result["provenance"]["git"]["source_tree_sha256"]


def test_latency_quantiles_are_explicit():
    assert _latencies(list(range(1, 21)))["p95"] == 19
    assert _latencies([1, 3])["p50"] == 2
    assert _latencies([])["samples"] == 0


def test_reports_are_immutable_and_concurrent_runs_are_refused(
    private_inputs,  # noqa: F811 - pytest fixture import
    tmp_path,
    monkeypatch,
):
    manifest, _, _, _, _ = private_inputs
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [{"name": "dense", "retrieval_mode": "dense"}],
            }
        )
    )
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-cache"))
    out = tmp_path / "report"
    run_benchmark(manifest, config, out)
    original = (out / "results.json").read_bytes()
    with pytest.raises(ContractError, match="overwrite"):
        run_benchmark(manifest, config, out)
    assert (out / "results.json").read_bytes() == original
    other = tmp_path / "concurrent"
    with _reserve_output(other), pytest.raises(ContractError, match="another run"):
        run_benchmark(manifest, config, other)
    assert not (other / ".real-eval.lock").exists()


def test_per_stage_timing_preserves_skipped_and_unavailable_statuses():
    class TimedService(SyntheticService):
        def retrieve(self, query, **kwargs):
            response = super().retrieve(query, **kwargs)
            response["details"]["stages"] = {
                "dense": {"status": "ok", "duration_ms": 4.0},
                "lexical": {"status": "skipped", "duration_ms": 0.0},
            }
            return response

    result = run_queries(
        TimedService(),
        [query()],
        {"name": "unit", "retrieval_mode": "dense", "reranker": "none"},
        {"top_k": 10, "latency_repetitions": 2},
    )
    assert result["stage_latency_ms"]["dense"]["p50"] == 4.0
    assert result["stage_latency_ms"]["dense"]["samples"] == 2
    assert result["stage_latency_ms"]["lexical"]["samples"] == 0
    assert result["stage_latency_ms"]["lexical"]["skipped_samples"] == 2
    assert len(result["per_query"][0]["stage_timings_ms"]) == 2
