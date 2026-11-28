"""Incremental index appends must match a full rebuild exactly.

Uses the deterministic hashing embedder (content-based, no model download)
and spies on ``generate_embeddings`` to count how many chunks each add embeds.
"""

from unittest.mock import MagicMock, Mock, patch

import faiss
import numpy as np
import pytest

from multilingual.video_library import VideoLibrary


def _segments(tag, count):
    return [
        {
            "start": float(i * 40),
            "end": float(i * 40 + 30),
            "text": f"{tag} topic {i}: glaze kiln clay wheel {tag}{i} firing notes.",
        }
        for i in range(count)
    ]


def _source(n):
    return {"platform": "podcast_rss", "url": f"https://radio.example/{n}"}


def _source_id(n):
    return f"pod_{n:016x}"


@pytest.fixture
def make_library(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_RAG_FORCE_HASH_EMBEDDINGS", "1")
    def build(name):
        metadata_client = Mock()
        metadata_client.fetch_many.side_effect = AssertionError("no metadata lookups")
        library = VideoLibrary(data_dir=tmp_path / name, metadata_client=metadata_client)
        real = library.processor.generate_embeddings
        library.embedded = []

        def spy(chunks):
            library.embedded.append(len(chunks))
            return real(chunks)

        library.processor.generate_embeddings = spy
        return library

    return build


def _add(library, n, count=6, language="en"):
    return library.add_transcribed_source(
        _source_id(n),
        _segments(f"s{n}", count),
        language=language,
        title=f"Episode {n}",
        source=_source(n),
    )


def _chunks(library, n):
    return len(library.videos[_source_id(n)]["chunks"])


def _vectors(index):
    return index.reconstruct_n(0, int(index.ntotal))


def _assert_same_as_rebuild(library):
    vectors = _vectors(library.index).copy()
    chunk_map = list(library.chunk_map)
    metadata = dict(library.index_embedding_metadata)
    library._rebuild_index()
    np.testing.assert_array_equal(vectors, _vectors(library.index))
    assert chunk_map == library.chunk_map
    assert metadata == library.index_embedding_metadata


def test_incremental_adds_match_full_rebuild(make_library):
    library = make_library("incremental")
    for n in range(4):
        _add(library, n, count=3 + n)
    # First add has nothing to append to; later adds embed only their chunks.
    assert library.embedded == [_chunks(library, n) for n in range(4)]
    assert isinstance(library.index, faiss.IndexFlatIP)
    assert int(library.index.ntotal) == len(library.chunk_map) == library.total_chunks
    _assert_same_as_rebuild(library)


def test_incremental_search_results_match_full_rebuild(make_library):
    incremental = make_library("inc")
    for n in range(4):
        _add(incremental, n)
    rebuilt = make_library("full")
    for n in range(4):
        rebuilt.videos[_source_id(n)] = incremental.videos[_source_id(n)]
    rebuilt._rebuild_index()

    for query in ("glaze kiln", "s2 topic 3 firing", "clay wheel s0"):
        got = incremental.search(query, k=8)
        want = rebuilt.search(query, k=8)
        assert [(r["video_id"], r["chunk_index"]) for r in got] == [
            (r["video_id"], r["chunk_index"]) for r in want
        ]
        assert [r["score"] for r in got] == pytest.approx([r["score"] for r in want])


def test_incremental_add_survives_save_and_load(make_library):
    library = make_library("persist")
    for n in range(3):
        _add(library, n)
    library.save()
    reloaded = VideoLibrary(data_dir=library.data_dir, processor=library.processor)
    reloaded.embedded = library.embedded
    library.embedded.clear()
    _add(reloaded, 3, count=2)
    assert library.embedded == [_chunks(reloaded, 3)]
    _assert_same_as_rebuild(reloaded)


def test_add_video_appends_only_new_chunks(make_library):
    library = make_library("youtube")
    _add(library, 0, count=4)
    transcript = [
        {"text": f"new video line {i} about glazes", "start": i * 40.0, "duration": 30.0}
        for i in range(5)
    ]
    with patch("multilingual.video_library.YouTubeTranscriptApi") as api_cls:
        api_cls.return_value = MagicMock(fetch=MagicMock(return_value=transcript))
        library.add_video("abc12345678", language="en")
    new_chunks = len(library.videos["abc12345678"]["chunks"])
    assert library.embedded == [_chunks(library, 0), new_chunks]
    assert library.chunk_map[-1] == ("abc12345678", new_chunks - 1)
    _assert_same_as_rebuild(library)


def test_replacing_a_source_falls_back_to_full_rebuild(make_library):
    library = make_library("replace")
    for n in range(3):
        _add(library, n)
    library.embedded.clear()
    _add(library, 1, count=2)  # same id, new transcript, keeps its position
    assert library.embedded == [library.total_chunks]
    first = _chunks(library, 0)
    assert library.chunk_map[first] == (_source_id(1), 0)
    _assert_same_as_rebuild(library)


def test_rechunk_falls_back_to_full_rebuild(make_library):
    library = make_library("rechunk")
    for n in range(3):
        _add(library, n)
    library.embedded.clear()
    library.rechunk_from_transcript(_source_id(2))
    assert library.embedded == [library.total_chunks]


def test_delete_falls_back_to_full_rebuild(make_library):
    library = make_library("delete")
    for n in range(3):
        _add(library, n)
    library.embedded.clear()
    library.remove_video(_source_id(0))
    assert library.embedded == [library.total_chunks]
    _assert_same_as_rebuild(library)


def test_embedding_metadata_change_falls_back_to_full_rebuild(make_library):
    library = make_library("metadata")
    for n in range(2):
        _add(library, n)
    library.embedded.clear()
    current = library.processor.embedding_metadata()
    library.processor.embedding_metadata = lambda: {**current, "revision": "next"}
    _add(library, 2, count=3)
    assert library.embedded == [library.total_chunks]
    assert library.index_embedding_metadata["revision"] == "next"


def test_preserved_saved_index_falls_back_to_full_rebuild(make_library):
    library = make_library("preserved")
    _add(library, 0)
    library._preserve_saved_index = True
    library.embedded.clear()
    _add(library, 1, count=2)
    assert library.embedded == [library.total_chunks]
    assert library._preserve_saved_index is False


def test_out_of_sync_chunk_map_falls_back_to_full_rebuild(make_library):
    library = make_library("drift")
    for n in range(2):
        _add(library, n)
    library.chunk_map = library.chunk_map[:-1]
    library.embedded.clear()
    _add(library, 2, count=2)
    assert library.embedded == [library.total_chunks]
    _assert_same_as_rebuild(library)


def test_failed_save_after_append_restores_previous_index(make_library, monkeypatch):
    library = make_library("rollback")
    for n in range(2):
        _add(library, n)
    index, chunk_map = library.index, library.chunk_map
    before = _vectors(index).copy()
    monkeypatch.setattr(library, "save", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        library.add_transcribed_source(
            _source_id(2), _segments("s2", 3), language="en",
            title="Episode 2", source=_source(2), persist=True,
        )
    assert library.index is index and library.chunk_map is chunk_map
    assert int(index.ntotal) == library.total_chunks
    np.testing.assert_array_equal(before, _vectors(index))
    assert _source_id(2) not in library.videos
