"""Exercise the real HTTP job routes and library with injected audio dependencies."""

import hashlib
import json
import threading
from contextlib import contextmanager
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from local_preview.audio_ingest import AudioIngestQueue
from local_preview.feeds import parse_feed
from local_preview.stt.base import TranscriptionResult, TranscriptionSegment
from local_preview.stt.fake_engine import FakeEngine
from multilingual.source_model import make_file_source_id, make_rss_source_id
from multilingual.video_library import VideoLibrary
from tests.test_eval_generator_api import _request
from tests.test_feeds import RSS
from tests.test_local_api_summary_and_retrieval import _make_service, local_api

FEED_URL = "https://radio.example/feed"
MEDIA_URL = "https://radio.example/boat.mp3"
AUDIO = b"synthetic sound bytes"
SEGMENT = TranscriptionSegment(1.25, 2.75, "Fold the paper.", [
    {"start": 1.25, "end": 1.65, "word": "Fold"},
])


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_RAG_FORCE_HASH_EMBEDDINGS", "1")
    monkeypatch.setenv("YT_RAG_KEEP_AUDIO", "1")
    monkeypatch.setenv("YT_RAG_AUDIO_MAX_MB", "500")
    monkeypatch.setenv("YT_RAG_STT_ENGINE", "faster_whisper")
    metadata = Mock()
    metadata.fetch_many.side_effect = AssertionError("Unexpected metadata lookup")
    library = VideoLibrary(data_dir=tmp_path / "data", metadata_client=metadata)
    service = _make_service()
    service.engine = SimpleNamespace(library=library)
    service.lock = threading.Lock()
    service._audio_ingest = None
    service.jobs = {}
    service.video_router = object()
    service._delete_ask_history = Mock()
    engine = FakeEngine(TranscriptionResult([SEGMENT], "en", 3, "fake", "fixture", True))
    feed = Mock(return_value=parse_feed(RSS, base_url=FEED_URL))

    @contextmanager
    def stream(url, **options):
        assert options["max_bytes"] == 500 * 1024 * 1024
        yield SimpleNamespace(
            url=url, headers={"Content-Type": "audio/mpeg"},
            iter_bytes=lambda: iter([AUDIO[:4], AUDIO[4:]]),
        )

    stream_media = Mock(side_effect=stream)
    monkeypatch.setattr(local_api, "AudioIngestQueue", partial(
        AudioIngestQueue, engine_factory=lambda _: engine, fetch_feed=feed, stream_media=stream_media,
        timer=iter(index * 0.75 for index in range(100)).__next__,
    ))
    monkeypatch.setattr(local_api, "SERVICE", service)
    monkeypatch.setattr(local_api, "ROOT_DIR", tmp_path)
    events = []
    done = threading.Event()
    update_job = service._update_job

    def record(job_id, **updates):
        update_job(job_id, **updates)
        if "step" in updates:
            events.append(updates["step"])
        if updates.get("status") in {"completed", "failed"}:
            done.set()

    service._update_job = record
    return SimpleNamespace(
        library=library, service=service, engine=engine, stream=stream_media, feed=feed,
        events=events, done=done, root=tmp_path,
    )


def _submit(app, **payload):
    app.done.clear()
    app.events.clear()
    status, _, data = _request("POST", "/v1/ingest/audio", {"language": "en", **payload})
    assert status == 202, data
    initial = json.loads(data)["job"]
    assert initial["status"] == initial["step"] == "queued"
    assert initial["progress"] == 0.0
    assert app.done.wait(5)
    status, _, data = _request("GET", "/v1/ingest/jobs/" + initial["job_id"])
    assert status == 200
    return json.loads(data)["job"]


@pytest.mark.parametrize("keep", [True, False])
def test_local_file_to_persisted_library(app, monkeypatch, keep):
    monkeypatch.setenv("YT_RAG_KEEP_AUDIO", str(int(keep)))
    source = app.root / "Paper.WAV"
    source.write_bytes(AUDIO)
    original = app.library.add_transcribed_source

    def check_lock(*args, **kwargs):
        assert app.service.library_write_lock._is_owned()
        return original(*args, **kwargs)

    monkeypatch.setattr(app.library, "add_transcribed_source", check_lock)
    job = _submit(app, kind="local_file", path=source.name)
    assert job["status"] == job["step"] == "completed", job
    assert job["source_type"] == "local_audio"
    assert job["attempts"] == 1
    assert job["progress"] == 1.0
    assert job["transcription_seconds"] == 0.75
    assert job["audio_duration_seconds"] == 3
    assert (job["stt_engine"], job["stt_model"]) == ("fake", "fixture")
    assert app.events == ["resolving", "validating", "transcribing", "indexing", "completed"]
    digest = hashlib.sha256(AUDIO).hexdigest()
    source_id = make_file_source_id(digest)
    assert job["video_id"] == source_id
    data = app.library.data_dir
    media = data / "media" / f"{source_id}.wav"
    words = data / "library" / "videos" / source_id / "stt_words.json"
    assert media.exists() is keep
    assert source.read_bytes() == AUDIO
    assert not list((data / "media").glob("*.part"))
    staged, language = app.engine.calls[0]
    assert staged != source and not staged.exists() and language == "en"
    assert words.stat().st_mode & 0o777 == 0o600
    assert json.loads(words.read_text()) == {
        "version": 1,
        "segments": [{"start": 1.25, "end": 2.75, "words": SEGMENT.words}],
    }
    if keep:
        assert media.stat().st_mode & 0o777 == 0o600
        assert media.read_bytes() == AUDIO
    assert app.service.video_router is None

    reloaded = VideoLibrary(data_dir=data, processor=app.library.processor)
    record = reloaded.videos[source_id]
    assert record["source"]["stt"]["audio_sha256"] == digest
    assert record["full_transcript"]["segments"] == [
        {"start": SEGMENT.start, "end": SEGMENT.end, "text": SEGMENT.text},
    ]
    assert record["source"]["local_media_path"] == (f"media/{media.name}" if keep else None)
    status, _, payload = _request("GET", "/v1/videos")
    assert status == 200
    listed, = json.loads(payload)["videos"]
    assert listed["video_id"] == source_id and listed["num_chunks"] > 0


def test_rss_enclosure_deduplicates_and_force_updates_retention(app, monkeypatch):
    job = _submit(app, kind="rss_episode", feed_url=FEED_URL, guid="fold-1")
    assert job["status"] == "completed", job
    source_id = make_rss_source_id(FEED_URL, "fold-1")
    assert job["video_id"] == source_id
    assert job["source_type"] == "podcast_rss"
    assert app.events == ["resolving", "downloading", "transcribing", "indexing", "completed"]
    assert app.engine.calls[0][0].name == f"{source_id}.mp3.part"
    app.feed.assert_called_once_with(FEED_URL)
    assert app.stream.call_args.args == (MEDIA_URL,)
    source = app.library.videos[source_id]["source"]
    assert source["show"]["name"] == "Paper Radio"
    assert source["episode"] == {
        "guid": "fold-1", "published_at": "2026-11-18T12:00:00+00:00", "duration_seconds": 3723,
    }
    media = app.library.data_dir / source["local_media_path"]
    assert media.read_bytes() == AUDIO

    again = _submit(app, kind="rss_episode", feed_url=FEED_URL, guid="fold-1")
    assert again["status"] == "completed"
    assert again["transcription_seconds"] is None
    assert again["audio_duration_seconds"] is None
    assert app.events == ["resolving", "completed"]
    app.feed.assert_called_once()
    app.stream.assert_called_once()
    assert len(app.engine.calls) == 1

    monkeypatch.setenv("YT_RAG_KEEP_AUDIO", "0")
    app.engine.result = TranscriptionResult([SEGMENT], "en", 3, "fake", "fixture", False)
    updated = _submit(app, kind="rss_episode", feed_url=FEED_URL, guid="fold-1", force=True)
    assert updated["status"] == "completed", updated
    assert not media.exists()
    assert not list((app.library.data_dir / "library").rglob("stt_words.json"))
    assert app.library.videos[source_id]["source"]["local_media_path"] is None


def test_failed_forced_publish_restores_previous_generation(app, monkeypatch):
    params = {"kind": "rss_episode", "feed_url": FEED_URL, "guid": "fold-1"}
    source_id = _submit(app, **params)["video_id"]
    previous = app.library.videos[source_id]
    media = app.library.data_dir / previous["source"]["local_media_path"]
    words = app.library.data_dir / "library" / "videos" / source_id / "stt_words.json"
    prior_words = words.read_bytes()
    app.engine.result = TranscriptionResult(
        [TranscriptionSegment(2, 3, "Paint the boat.")], "en", 3, "fake", "fixture", False,
    )

    @contextmanager
    def replacement(url, **kwargs):
        yield SimpleNamespace(url=url, headers={}, iter_bytes=lambda: iter([b"replacement bytes"]))

    app.stream.side_effect = replacement
    save = app.library.save

    def fail_after_write():
        save()
        raise OSError("Synthetic persistence error")

    monkeypatch.setattr(app.library, "save", fail_after_write)
    failed = _submit(app, **params, force=True)
    assert failed["status"] == "failed"
    assert failed["step"] == "indexing"
    assert failed["error_code"] == "AUDIO_INGEST_FAILED"
    assert app.library.videos[source_id] is previous
    reloaded = VideoLibrary(data_dir=app.library.data_dir, processor=app.library.processor)
    assert reloaded.videos[source_id]["full_transcript"] == previous["full_transcript"]
    assert media.read_bytes() == AUDIO
    assert words.read_bytes() == prior_words
    assert not list(media.parent.glob("*.part"))


def test_never_downloads_youtube_media(app):
    app.feed.return_value["items"][0]["media_url"] = "https://cdn.googlevideo.com/paper.mp3"
    job = _submit(app, kind="rss_episode", feed_url=FEED_URL, guid="fold-1")
    assert job["status"] == "failed"
    assert "YouTube audio" in job["error_message"]
    app.stream.assert_not_called()
    assert not app.engine.calls


def test_local_cap_cleans_up_partial_copy(app, monkeypatch):
    monkeypatch.setenv("YT_RAG_AUDIO_MAX_MB", "1")
    source = app.root / "large.wav"
    source.write_bytes(b"0" * (1024 * 1024 + 1))
    job = _submit(app, kind="local_file", path=str(source))
    assert job["status"] == "failed"
    assert job["step"] == "validating"
    assert "YT_RAG_AUDIO_MAX_MB" in job["error_message"]
    assert not app.engine.calls
    assert not list((app.library.data_dir / "media").iterdir())


def test_a_single_worker_processes_jobs_in_order(app, monkeypatch):
    first_started = threading.Event()
    unblock_first = threading.Event()
    threads = []
    transcribe = app.engine.transcribe

    def controlled_transcribe(path, language):
        threads.append(threading.get_ident())
        if len(threads) == 1:
            first_started.set()
            assert unblock_first.wait(5)
        return transcribe(path, language)

    monkeypatch.setattr(app.engine, "transcribe", controlled_transcribe)
    first_path, second_path = app.root / "first.wav", app.root / "second.wav"
    first_path.write_bytes(b"first sound")
    second_path.write_bytes(b"second sound")
    first = app.service.start_audio_ingest({
        "kind": "local_file", "path": str(first_path), "language": "en"
    })
    try:
        assert first_started.wait(5)
        second = app.service.start_audio_ingest({
            "kind": "local_file", "path": str(second_path), "language": "en"
        })
        assert app.service.get_job(first.job_id).step == "transcribing"
        assert app.service.get_job(second.job_id).status == "queued"
    finally:
        unblock_first.set()
        worker_idle = threading.Event()

        def wait_for_jobs():
            app.service._audio_ingest.pending.join()
            worker_idle.set()

        threading.Thread(target=wait_for_jobs, daemon=True).start()
        assert worker_idle.wait(5)
    assert app.service.get_job(first.job_id).status == "completed"
    assert app.service.get_job(second.job_id).status == "completed"
    assert len(threads) == 2 and len(set(threads)) == 1


def test_legacy_reingest_and_delete_share_library_write_lock(app, monkeypatch):
    source = app.root / "paper.wav"
    source.write_bytes(AUDIO)
    job = _submit(app, kind="local_file", path=str(source))
    source_id = job["video_id"]
    for name in ("rechunk_from_transcript", "remove_video", "save"):
        original = getattr(app.library, name)

        def locked(*args, call=original, **kwargs):
            assert app.service.library_write_lock._is_owned()
            return call(*args, **kwargs)

        monkeypatch.setattr(app.library, name, locked)
    result = app.service.ingest(url=source_id, mode="single", language="en", force=True)
    assert result["jobs"][0]["status"] == "completed"
    app.service.delete_video(source_id)
    assert source_id not in app.library.videos


def test_preview_capabilities_validation_and_csrf(app, monkeypatch):
    monkeypatch.setattr(local_api, "preview_feed", app.feed)
    status, _, data = _request("GET", "/v1/stt/capabilities")
    assert status == 200
    assert json.loads(data)["capabilities"]["default_engine"] == "faster_whisper"
    status, _, data = _request("POST", "/v1/feeds/preview", {"url": FEED_URL})
    assert status == 200
    assert json.loads(data)["items"][0]["external_id"] == "fold-1"
    assert app.service._audio_ingest is None and not app.service.jobs
    assert _request("POST", "/v1/ingest/audio", {"kind": "invalid"})[0] == 400
    for route in ("/v1/feeds/preview", "/v1/ingest/audio"):
        assert _request(
            "POST", route, {}, request_headers={"Origin": "http://127.0.0.1:8000"},
            authenticated=False,
        )[0] == 403
    app.feed.assert_called_once_with(FEED_URL)
