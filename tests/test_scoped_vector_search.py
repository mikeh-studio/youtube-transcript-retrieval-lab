"""Compare scoped FAISS retrieval against the former full-search/filter path."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import faiss
import numpy as np
import pytest

from multilingual.video_library import VideoLibrary


class VectorProcessor:
    def __init__(self, dimension):
        self.dimension = dimension
        self.query = np.zeros((1, dimension), dtype=np.float32)
        self.language = None

    def encode_query(self, query, language=None):
        self.language = language
        return self.query

    def generate_embeddings(self, chunks):
        return np.asarray([chunk["vector"] for chunk in chunks], dtype=np.float32)

    def embedding_metadata(self):
        return {"backend": "hashing", "model": "test", "dim": self.dimension}


def make_library(tmp_path, *, dimension=128, size=150, tied=False):
    processor = VectorProcessor(dimension)
    library = VideoLibrary(data_dir=str(tmp_path), processor=processor)
    rng = np.random.default_rng(42)
    vectors = rng.standard_normal((size, dimension), dtype=np.float32)
    if tied:
        vectors[:] = vectors[0]
    else:
        # Include duplicate vectors as well as distinct scores.
        vectors[::4] = vectors[0]
    processor.query = vectors[:1]
    library.videos = {
        f"video-{i}": {
            "title": f"Title {i}",
            "url": f"https://www.youtube.com/watch?v=video-{i}",
            "language": "en" if i % 2 else "ja",
            "chunks": [],
        }
        for i in range(5)
    }
    library.chunk_map = []
    # Interleaving exercises selectors that are not contiguous ranges.
    for position, vector in enumerate(vectors):
        video_id = f"video-{position % 5}"
        chunks = library.videos[video_id]["chunks"]
        library.chunk_map.append((video_id, len(chunks)))
        chunks.append(
            {
                "raw_text": f"Evidence {position}",
                "embed_text": f"Evidence {position}",
                "start": position * 3.5,
                "end": position * 3.5 + 3,
                "vector": vector.tolist(),
            }
        )
    library.index = faiss.IndexFlatIP(dimension)
    library.index.add(vectors)
    library.index_embedding_metadata = processor.embedding_metadata()
    return library


def legacy_search(library, **kwargs):
    with patch.object(
        library,
        "_search_scoped_index",
        side_effect=lambda query, ids: library.index.search(
            query, library.index.ntotal
        ),
    ):
        return library.search("query", **kwargs)


@pytest.mark.parametrize("dimension", [4, 128, 768, 1024])
@pytest.mark.parametrize("tied", [False, True])
def test_scoped_scores_order_and_citations_exactly_match_legacy(
    tmp_path, dimension, tied
):
    library = make_library(tmp_path, dimension=dimension, tied=tied)
    for scope in (
        {"video_id": "video-1"},
        {"video_ids": ["video-0", "video-3"]},
        {"video_ids": list(library.videos)},
    ):
        for k in (1, 5, 40, 1000):
            expected = legacy_search(library, k=k, **scope)
            assert library.search("query", k=k, **scope) == expected
            assert all(
                row["url"].endswith(f"&t={int(row['start'])}s") for row in expected
            )


def test_zero_query_ties_and_reordered_duplicate_scope_ids(tmp_path):
    library = make_library(tmp_path)
    library.processor.query[:] = 0
    ids = ["video-3", "video-0", "video-3"]
    expected = legacy_search(library, k=5, video_ids=ids)
    assert library.search("query", k=5, video_ids=ids) == expected
    assert library.search("query", k=5, video_ids=list(reversed(ids))) == expected


def test_scoped_call_selects_only_matching_vectors(tmp_path):
    library = make_library(tmp_path, size=1000)
    with patch.object(library.index, "search", wraps=library.index.search) as search:
        results = library.search("query", k=5, video_id="video-2")
    assert len(results) == 5
    args, kwargs = search.call_args
    assert args[1] == 200 < library.index.ntotal
    selector = kwargs["params"].sel
    assert all(bool(selector.is_member(i)) == (i % 5 == 2) for i in range(1000))


def test_unscoped_call_keeps_original_top_k_path(tmp_path):
    library = make_library(tmp_path)
    with patch.object(library.index, "search", wraps=library.index.search) as search:
        library.search("query", k=5)
    assert search.call_args.args[1] == 5
    assert search.call_args.kwargs == {}


def test_empty_invalid_and_oversized_scopes(tmp_path):
    library = make_library(tmp_path)
    library.videos["empty"] = {"chunks": []}
    assert library.search("query", video_ids=[]) == []
    assert library.search("query", video_id="empty") == []
    assert library.search("query", video_id="video-1", k=0) == []
    assert len(library.search("query", video_id="video-1", k=1000)) == 30
    with pytest.raises(KeyError):
        library.search("query", video_ids=["missing"])
    with pytest.raises(ValueError):
        library.search("query", video_id="video-1", video_ids=["video-2"])
    with pytest.raises(TypeError):
        library.search("query", video_ids="video-1")


def test_scope_language_and_explicit_query_language_unchanged(tmp_path):
    library = make_library(tmp_path)
    library.search("query", video_id="video-1")
    assert library.processor.language == "en"
    library.search("query", video_id="video-1", language="ja")
    assert library.processor.language == "ja"


def test_rebuild_delete_and_reload_do_not_reuse_stale_vector_ids(tmp_path):
    library = make_library(tmp_path)
    library._rebuild_index()  # Canonical storage uses video-grouped vector order.
    scope = {"video_ids": ["video-0", "video-3"], "k": 8}
    assert library.search("query", **scope) == legacy_search(library, **scope)
    del library.videos["video-1"]
    library.videos["video-0"]["chunks"].reverse()
    library.videos["video-3"]["chunks"].pop()
    library._rebuild_index()
    assert library.search("query", **scope) == legacy_search(library, **scope)
    expected = library.search("query", **scope)
    library.save()
    loaded = VideoLibrary(data_dir=str(tmp_path), processor=library.processor)
    assert loaded.search("query", **scope) == expected
    assert loaded.search("query", **scope) == legacy_search(loaded, **scope)


def test_other_index_types_keep_legacy_behavior(tmp_path):
    library = make_library(tmp_path)
    vectors = library.index.reconstruct_n()
    library.index = faiss.IndexFlatL2(vectors.shape[1])
    library.index.add(vectors)
    assert library.search("query", video_id="video-0") == legacy_search(
        library, video_id="video-0"
    )


def test_concurrent_read_only_scopes_have_identical_results(tmp_path):
    library = make_library(tmp_path)
    scopes = [{"video_id": video_id, "k": 7} for video_id in library.videos] * 4
    expected = [legacy_search(library, **scope) for scope in scopes]
    with ThreadPoolExecutor(max_workers=4) as pool:
        actual = list(pool.map(lambda scope: library.search("query", **scope), scopes))
    assert actual == expected
