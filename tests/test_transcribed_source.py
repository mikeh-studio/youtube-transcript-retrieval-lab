"""Exercise indexed audio through the library and local service without providers."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from local_preview.grounded_answer import build_citation_catalog, build_retrieved_chunks_payload
from multilingual.video_library import VideoLibrary
from multilingual.video_routing import build_routing_profile
from retrieval.transcript_tools import TranscriptRetrievalTools
from tests.test_eval_generator_api import _request
from tests.test_local_api_summary_and_retrieval import _make_service, local_api


SOURCE_ID = "pod_0123456789abcdef"
PAGE = "https://radio.example/kiln"
MEDIA = "https://radio.example/kiln.mp3"
SEGMENTS = [
    {"start": 12.5, "end": 18.0, "text": "Let the blue glaze cool.", "speaker": None},
    {"start": 78, "duration": 4.0, "text": "Place the cup on a felt mat."},
]


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_RAG_FORCE_HASH_EMBEDDINGS", "1")
    metadata_client = Mock()
    metadata_client.fetch_many.side_effect = AssertionError("Unexpected metadata lookup")
    return VideoLibrary(data_dir=tmp_path, metadata_client=metadata_client)


def add_episode(library):
    return library.add_transcribed_source(
        SOURCE_ID,
        SEGMENTS,
        language="en",
        title=f"Video {SOURCE_ID}",
        source={
            "platform": "podcast_rss",
            "url": PAGE,
            "media_url": MEDIA,
            "show": {"name": "Kiln radio"},
        },
    )


def test_add_save_and_load_transcribed_source(library):
    assert add_episode(library) == SOURCE_ID
    video = library.videos[SOURCE_ID]
    assert video["full_transcript"]["segments"] == [
        SEGMENTS[0],
        {"start": 78.0, "end": 82.0, "text": SEGMENTS[1]["text"]},
    ]
    listed, = library.list_videos()
    assert listed["url"] == PAGE
    assert listed["num_chunks"] == 2
    assert listed["source"]["show"]["name"] == "Kiln radio"
    assert listed["chunking_stale"] is False
    assert library.backfill_source_metadata(force=True)["skipped"] == [SOURCE_ID]
    library.metadata_client.fetch_many.assert_not_called()

    library.save()
    for relative in (
        "library/library.json",
        f"library/videos/{SOURCE_ID}/record.json",
        "index/library.faiss",
    ):
        assert (library.data_dir / relative).stat().st_mode & 0o777 == 0o600
    reloaded = VideoLibrary(data_dir=library.data_dir, processor=library.processor)
    assert reloaded.videos[SOURCE_ID] == video
    result, = reloaded.search("blue glaze", video_id=SOURCE_ID, k=1)
    assert result["url"] == MEDIA + "#t=12"
    assert result["video_url"] == PAGE
    assert result["source"] == video["source"]


def test_retrieval_and_citation_links_follow_the_source(library, monkeypatch):
    add_episode(library)
    service = _make_service()
    service.engine = SimpleNamespace(library=library)
    monkeypatch.setattr(local_api, "SERVICE", service)
    fetch_title = Mock(side_effect=AssertionError("Unexpected title lookup"))
    monkeypatch.setattr(local_api, "fetch_video_title", fetch_title)
    status, _, payload = _request("GET", "/v1/videos")
    assert status == 200
    listed, = json.loads(payload)["videos"]
    assert listed["source_type"] == "podcast_rss"
    assert listed["url"] == PAGE
    fetch_title.assert_not_called()
    row = service._all_chunks(video_id=SOURCE_ID)[0]
    assert row["url"] == MEDIA + "#t=12"
    context = TranscriptRetrievalTools(library=library, retrieve_fn=None).read_context(
        SOURCE_ID, timestamp=14
    )
    assert context["url"] == row["url"]
    del row["url"], row["video_url"]
    for build in (build_citation_catalog, build_retrieved_chunks_payload):
        citation, = build([row])
        assert citation["source_type"] == "transcript"
        assert citation["source"]["platform"] == "podcast_rss"
        assert citation["video_url"] == PAGE
        assert citation["url"] == MEDIA + "#t=12"
    profile = build_routing_profile(SOURCE_ID, library.videos[SOURCE_ID])
    assert "Show: Kiln radio" in profile["identity_text"]
    assert f"URL: {PAGE}" in profile["identity_text"]


def test_ingest_rechunks_stale_audio_from_canonical_segments(library, monkeypatch):
    add_episode(library)
    video = library.videos[SOURCE_ID]
    transcript = video["full_transcript"]
    video["chunks"] = [{"raw_text": "stale"}]
    video["chunking"] = library.legacy_chunking_metadata()
    add_video = Mock(side_effect=AssertionError("Unexpected transcript acquisition"))
    monkeypatch.setattr(library, "add_video", add_video)

    service = _make_service()
    service.engine = SimpleNamespace(library=library)
    service.lock = threading.Lock()
    service.jobs = {}
    service.video_router = object()
    monkeypatch.setattr(local_api, "SERVICE", service)
    status, _, payload = _request(
        "POST", "/v1/ingest/videos", {"url": SOURCE_ID, "language": "ja"}
    )
    assert status == 200
    result = json.loads(payload)

    job, = result["jobs"]
    assert job["status"] == "completed"
    assert job["language"] == "en"
    status, _, payload = _request("GET", "/v1/ingest/jobs")
    assert status == 200
    saved_job, = json.loads(payload)["jobs"]
    assert (saved_job["job_id"], saved_job["status"], saved_job["url"]) == (
        job["job_id"], "completed", PAGE
    )
    assert service.video_router is None
    add_video.assert_not_called()
    library.metadata_client.fetch_many.assert_not_called()
    assert library.videos[SOURCE_ID]["full_transcript"] == transcript
    assert not library.video_chunking_is_stale(SOURCE_ID)
    reloaded = VideoLibrary(data_dir=library.data_dir, processor=library.processor)
    assert reloaded.videos[SOURCE_ID]["full_transcript"] == transcript
    assert reloaded.videos[SOURCE_ID]["chunks"][0]["raw_text"] == SEGMENTS[0]["text"]
    again = service.ingest(url=SOURCE_ID, mode="single", language="ja", force=False)
    assert again["queued_count"] == 0
    assert again["skipped"] == [{"video_id": SOURCE_ID, "reason": "already_indexed"}]


def test_rechunk_keeps_prior_record_and_index_on_embedding_failure(library, monkeypatch):
    add_episode(library)
    video = library.videos[SOURCE_ID]
    video["chunking"] = library.legacy_chunking_metadata()
    index = library.index
    chunk_map = library.chunk_map
    failure = Mock(side_effect=RuntimeError("Synthetic embedding failure"))
    monkeypatch.setattr(library.processor, "generate_embeddings", failure)

    with pytest.raises(RuntimeError, match="Synthetic embedding failure"):
        library.rechunk_from_transcript(SOURCE_ID)
    assert library.videos[SOURCE_ID] is video
    assert library.index is index
    assert library.chunk_map is chunk_map
    assert library.video_chunking_is_stale(SOURCE_ID)
