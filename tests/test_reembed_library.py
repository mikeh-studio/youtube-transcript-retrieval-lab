"""The full re-embed command rebuilds every vector from stored chunks."""

from unittest.mock import Mock

import faiss
import numpy as np
import pytest

from multilingual.video_library import VideoLibrary
from pipelines import reembed_library


@pytest.fixture
def saved_library(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_RAG_FORCE_HASH_EMBEDDINGS", "1")
    library = VideoLibrary(data_dir=tmp_path, metadata_client=Mock())
    for n in range(3):
        library.add_transcribed_source(
            f"pod_{n:016x}",
            [
                {"start": i * 50.0, "end": i * 50.0 + 40, "text": f"s{n} glaze kiln note {i}"}
                for i in range(4)
            ],
            language="en",
            title=f"Episode {n}",
            source={"platform": "podcast_rss", "url": f"https://radio.example/{n}"},
        )
    library.save()
    return library


def _load(path):
    return VideoLibrary(data_dir=path, metadata_client=Mock())


def _vectors(library):
    return library.index.reconstruct_n(0, int(library.index.ntotal))


def test_reembed_replaces_drifted_vectors_and_saves(saved_library):
    expected = _vectors(saved_library).copy()
    # Simulate a stale vector left behind by an earlier incremental add.
    drifted = expected.copy()
    drifted[1] = -drifted[1]
    index = faiss.IndexFlatIP(drifted.shape[1])
    index.add(drifted)
    saved_library.index = index
    saved_library.save()

    assert reembed_library.main(["--data-dir", str(saved_library.data_dir), "--allow-hash"]) == 0
    reloaded = _load(saved_library.data_dir)
    np.testing.assert_array_equal(_vectors(reloaded), expected)
    assert reloaded.chunk_map == saved_library.chunk_map


def test_reembed_refuses_hashing_without_flag(saved_library):
    calls = []
    saved_library.processor.generate_embeddings = lambda chunks: calls.append(chunks)
    result = reembed_library.reembed(saved_library, log=lambda *_: None)
    assert result["status"] == "refused"
    assert calls == []


def test_reembed_main_refusal_exit_code(saved_library):
    assert reembed_library.main(["--data-dir", str(saved_library.data_dir)]) == 2


def test_reembed_dry_run_does_not_embed(saved_library):
    calls = []
    saved_library.processor.generate_embeddings = lambda chunks: calls.append(chunks)
    result = reembed_library.reembed(
        saved_library, allow_hash=True, dry_run=True, log=lambda *_: None
    )
    assert result["status"] == "dry_run"
    assert result["sources"] == 3
    assert result["chunks"] == saved_library.total_chunks
    assert calls == []


def test_reembed_missing_data_dir(tmp_path):
    assert reembed_library.main(["--data-dir", str(tmp_path / "missing")]) == 1
