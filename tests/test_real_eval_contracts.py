"""Contract tests use synthetic text and labels, never curated benchmark evidence."""

import json
import subprocess

import pytest

from evals.contracts import (
    ContractError,
    file_sha256,
    load_config,
    load_corpus,
    load_dataset,
    load_manifest,
    require_private,
)


@pytest.fixture
def private_inputs(tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "videos": [
                    {
                        "video_id": "synthetic",
                        "title": "Unit test",
                        "language": "en",
                        "chunks": [
                            {"start": 0, "end": 2, "raw_text": "Synthetic unit test."}
                        ],
                    }
                ],
            }
        )
    )
    row = {
        "id": "synthetic-q",
        "query": "unit",
        "language": "en",
        "query_type": "factual",
        "difficulty": "easy",
        "group": "unit",
        "split": "dev",
        "unanswerable": False,
        "judgments": {"synthetic:0": 2},
        "judgment_source": "synthetic_unit_test",
    }
    dataset = tmp_path / "dev.jsonl"
    dataset.write_text(json.dumps(row) + "\n")
    manifest = tmp_path / "manifest.json"
    payload = {
        "schema_version": 1,
        "evidence_level": "real_component_smoke",
        "embedding": {"model": "intfloat/multilingual-e5-base", "revision": "a" * 40},
        "corpus": {"path": corpus.name, "sha256": file_sha256(corpus)},
        "datasets": {"dev": {"path": dataset.name, "sha256": file_sha256(dataset)}},
    }
    manifest.write_text(json.dumps(payload))
    return manifest, corpus, dataset, row, payload


def test_manifest_hashes_and_split_default(private_inputs):
    manifest, corpus, dataset, _, _ = private_inputs
    actual = load_manifest(manifest)
    assert actual["corpus_path"] == corpus
    assert actual["dataset_path"] == dataset
    assert actual["split"] == "dev"
    dataset.write_text(dataset.read_text() + "\n")
    with pytest.raises(ContractError, match="SHA-256 mismatch"):
        load_manifest(manifest)


def test_holdout_is_explicit_and_dev_does_not_open_holdout(private_inputs):
    manifest, _, _, _, payload = private_inputs
    payload["datasets"]["holdout"] = {"path": "DO_NOT_READ", "sha256": "0" * 64}
    manifest.write_text(json.dumps(payload))
    assert load_manifest(manifest)["split"] == "dev"
    with pytest.raises(ContractError, match="explicit opt-in"):
        load_manifest(manifest, split="holdout")


@pytest.mark.parametrize("revision", ["main", "latest", "a" * 39, "A" * 40, ""])
def test_manifest_requires_immutable_revision(private_inputs, revision):
    manifest, _, _, _, payload = private_inputs
    payload["embedding"]["revision"] = revision
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ContractError, match="immutable"):
        load_manifest(manifest)


def test_manifest_requires_honest_evidence_label(private_inputs):
    manifest, _, _, _, payload = private_inputs
    payload["evidence_level"] = "validated_production_benchmark"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ContractError, match="evidence_level"):
        load_manifest(manifest)


@pytest.mark.parametrize("grade", [True, -1, 3, 1.0, "2"])
def test_rejects_invalid_relevance_grades(private_inputs, grade):
    _, _, dataset, row, _ = private_inputs
    row["judgments"]["synthetic:0"] = grade
    dataset.write_text(json.dumps(row))
    with pytest.raises(ContractError, match="integers 0, 1, or 2"):
        load_dataset(dataset, split="dev", chunk_ids={"synthetic:0"})


def test_rejects_unknown_evidence_and_split_mismatch(private_inputs):
    _, _, dataset, row, _ = private_inputs
    with pytest.raises(ContractError, match="unknown corpus"):
        load_dataset(dataset, split="dev", chunk_ids={"other:0"})
    row["split"] = "holdout"
    dataset.write_text(json.dumps(row))
    with pytest.raises(ContractError, match="selected split"):
        load_dataset(dataset, split="dev", chunk_ids={"synthetic:0"})


def test_unanswerable_cannot_have_positive_labels(private_inputs):
    _, _, dataset, row, _ = private_inputs
    row["unanswerable"] = True
    dataset.write_text(json.dumps(row))
    with pytest.raises(ContractError, match="unanswerable"):
        load_dataset(dataset, split="dev", chunk_ids={"synthetic:0"})
    row["judgments"]["synthetic:0"] = 0
    dataset.write_text(json.dumps(row))
    assert load_dataset(dataset, split="dev", chunk_ids={"synthetic:0"})[0][
        "unanswerable"
    ]


def test_corpus_never_accepts_fixture_scores(private_inputs):
    _, corpus, _, _, _ = private_inputs
    data = json.loads(corpus.read_text())
    data["videos"][0]["chunks"][0]["dense_scores"] = {"query": 0.9}
    corpus.write_text(json.dumps(data))
    with pytest.raises(ContractError, match="fixture scores/vectors"):
        load_corpus(corpus)


def test_config_rejects_provider_or_fixture_modes(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"schema_version": 1, "corpus_fixture": "bad", "runs": []})
    )
    with pytest.raises(ContractError, match="config supports"):
        load_config(path)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [{"name": "bad", "retrieval_mode": "agentic"}],
            }
        )
    )
    with pytest.raises(ContractError, match="retrieval_mode"):
        load_config(path)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [
                    {
                        "name": "rerank",
                        "retrieval_mode": "dense",
                        "reranker": "cross_encoder",
                    }
                ],
            }
        )
    )
    with pytest.raises(ContractError, match="immutable revision"):
        load_config(path)


def test_private_paths_require_ignore_and_cannot_be_tracked(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    path = tmp_path / "labels.jsonl"
    path.write_text("private")
    with pytest.raises(ContractError, match="gitignored"):
        require_private(path, root=tmp_path)
    (tmp_path / ".gitignore").write_text("labels.jsonl\n")
    require_private(path, root=tmp_path)
    subprocess.run(["git", "add", "-f", "labels.jsonl"], cwd=tmp_path, check=True)
    with pytest.raises(ContractError, match="must not be tracked"):
        require_private(path, root=tmp_path)
