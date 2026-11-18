"""Feed polling with synthetic metadata, transcripts and fake clocks."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.message import Message
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import Mock
from xml.sax.saxutils import escape

import pytest
from youtube_transcript_api import IpBlocked, RequestBlocked

from local_preview import subscriptions
from local_preview.feeds import FetchResult
from local_preview.subscriptions import FeedPollError, SubscriptionStore
from local_preview.stt.base import TranscriptionResult
from multilingual import video_library
from multilingual.source_model import make_rss_source_id
from tests.test_audio_ingest import SEGMENT, app as audio_app
from tests.test_eval_generator_api import _request
from tests.test_local_api_summary_and_retrieval import _make_service, local_api

PODCAST = "https://radio.example/feed"
YOUTUBE = "https://www.youtube.com/feeds/videos.xml?channel_id=synthetic"
PUBLISHED = "2026-11-17T12:00:00+00:00"


class Clock:
    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def _rss(*items):
    entries = "".join(
        f"""<item><title>{escape(title)}</title><guid>{guid}</guid>
        <pubDate>Tue, 17 Nov 2026 12:00:00 GMT</pubDate>
        <itunes:duration>{duration}</itunes:duration>
        <enclosure url="https://radio.example/{guid}.mp3" type="audio/mpeg"/></item>"""
        for guid, title, duration in items
    )
    return f'<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><title>Paper Radio</title>{entries}</channel></rss>'.encode()


def _atom(*ids):
    entries = "".join(
        f"<entry><title>Paper Lesson</title><yt:videoId>{video_id}</yt:videoId>"
        f"<published>{PUBLISHED}</published></entry>" for video_id in ids
    )
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
            f"{entries}</feed>").encode()


def _fetched(body=b"", *, url=PODCAST, status=200, **headers):
    return FetchResult(body, url, status, headers)


def _success(_subscription, item, _show):
    return {"status": "ingested", "reason": None, "source_id": item["external_id"]}


def test_conditional_get_deduplicates_and_release_state_reopens(tmp_path):
    body = _rss(("fold", "Fold a Boat", 800), ("fold", "Fold a Duck", 800))
    modified = "Tue, 17 Nov 2026 12:00:00 GMT"
    fetch = Mock(side_effect=[
        _fetched(body, **{"eTAG": '"paper"', "Last-Modified": modified}),
        _fetched(status=304),
        _fetched(body),
    ])
    clock = Clock()
    store = SubscriptionStore(tmp_path, fetch=fetch, clock=clock)
    subscription_id = store.create({"url": PODCAST})["id"]
    first = store.poll(subscription_id)
    assert first["new_count"] == 1
    assert first["releases"][0]["published_at"] == PUBLISHED
    assert first["releases"][0]["status"] == "discovered"
    assert fetch.call_args.kwargs["headers"] == {}

    reopened = SubscriptionStore(tmp_path, fetch=fetch, clock=clock)
    assert reopened.poll(subscription_id)["not_modified"]
    assert fetch.call_args.kwargs["headers"] == {
        "If-None-Match": '"paper"', "If-Modified-Since": modified,
    }
    clock.advance(1)
    assert reopened.poll(subscription_id)["new_count"] == 0
    assert reopened.get(subscription_id)["etag"] is None
    assert reopened.get(subscription_id)["last_polled_at"] == clock().isoformat()
    assert reopened.list_releases("2026-11-18T20:00:00+08:00") == first["releases"]
    assert reopened.list_releases(clock().isoformat()) == []
    for name in ("subscriptions.json", "releases.jsonl"):
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o600
    assert len((tmp_path / "releases.jsonl").read_text().splitlines()) == 1


def test_filters_and_dry_run_do_not_ingest_or_mutate(tmp_path):
    fetch = Mock(return_value=_fetched(_rss(
        ("other", "Glue a Boat", 900), ("preview", "Fold a Boat preview", 900),
        ("main", "Fold a Boat", 40),
    )))
    ingest = Mock(side_effect=_success)
    store = SubscriptionStore(tmp_path, fetch=fetch, ingest_podcast=ingest, clock=Clock())
    subscription = store.create({
        "url": PODCAST, "auto_ingest": True, "title_include": "^Fold", "title_exclude": "preview$",
    })
    subscription_id = subscription["id"]
    assert subscription["min_duration_s"] == 600
    result = store.poll(subscription_id, dry_run=True)
    assert [row["reason"] for row in result["releases"]] == ["title_include", "title_exclude", None]
    ingest.assert_not_called()
    assert store.get(subscription_id) == subscription
    assert not store.releases_path.exists()

    actual = store.poll(subscription_id)
    assert actual["new_count"] == 3 and len(actual["releases"]) == 3
    assert actual["attempted_count"] == 1
    assert ingest.call_args.args[1]["duration_seconds"] == 40
    assert [row["status"] for row in actual["releases"]] == ["skipped", "skipped", "ingested"]


def test_thirty_clip_flood_cap_counts_skips_and_drains_after_304(tmp_path):
    clock = Clock()
    ids = [f"synthetic{i:02}" for i in range(30)]
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=first"
    fetch = Mock(side_effect=[
        _fetched(_atom(*ids), url=url, ETag='"first"'),
        _fetched(_atom("synthetic99"), url=YOUTUBE, ETag='"second"'),
        *[_fetched(url=url, status=304)] * 6,
    ])
    pause = Mock()
    ingest = Mock(return_value={"status": "skipped", "reason": "min_duration_s", "duration_seconds": 22})
    store = SubscriptionStore(tmp_path, fetch=fetch, ingest_youtube=ingest, clock=clock, pause=pause)
    subscription_id = store.create({"url": url, "auto_ingest": True})["id"]
    second_id = store.create({"url": YOUTUBE, "auto_ingest": True})["id"]
    first, second = store.poll_all()
    assert (first["new_count"], first["attempted_count"], first["pending_count"]) == (30, 5, 25)
    assert (second["subscription_id"], second["attempted_count"]) == (second_id, 1)
    reopened = SubscriptionStore(tmp_path, fetch=fetch, ingest_youtube=ingest, clock=clock, pause=pause)
    clock.advance(600)
    for remaining in (20, 15, 10, 5, 0):
        polled = reopened.poll(subscription_id)
        assert polled["not_modified"] and polled["new_count"] == 0
        assert (polled["attempted_count"], polled["pending_count"]) == (5, remaining)
    assert reopened.poll(subscription_id)["attempted_count"] == 0
    assert len(reopened.list_releases()) == 31
    assert [call.args[1]["external_id"] for call in ingest.call_args_list] == [
        *ids[:5], "synthetic99", *ids[5:],
    ]
    assert [call.args for call in pause.call_args_list] == [(1.2,)] * 31


def test_throttle_is_persistent_and_shared_across_youtube_feeds(tmp_path):
    clock = Clock()
    fetch = Mock(side_effect=lambda url, **kwargs: _fetched(_atom("synthetic00", "synthetic01"), url=url))
    paused = {"status": "pending", "reason": "rate_limited", "retry_after": 90}
    outcomes = iter([paused, paused])
    ingest = Mock(side_effect=lambda *args: next(outcomes, None) or _success(*args))
    store = SubscriptionStore(tmp_path, fetch=fetch, ingest_youtube=ingest, clock=clock, pause=Mock())
    first, second = [
        store.create({"url": YOUTUBE + name, "auto_ingest": True})["id"]
        for name in ("1", "2")
    ]
    results = store.poll_all()
    assert [row["attempted_count"] for row in results] == [1, 0]
    assert results[0]["releases"][0]["reason"] == "rate_limited"

    reopened = SubscriptionStore(tmp_path, fetch=fetch, ingest_youtube=ingest, clock=clock, pause=Mock())
    clock.advance(89)
    assert reopened.poll(first)["attempted_count"] == 0
    assert reopened.poll(second)["attempted_count"] == 0
    clock.advance(1)
    assert reopened.poll(first)["attempted_count"] == 1
    clock.advance(119)
    assert reopened.poll(first)["attempted_count"] == 0
    clock.advance(1)
    assert reopened.poll(first)["attempted_count"] == 2
    assert reopened.list_releases()[0]["reason"] is None


def test_feed_failure_keeps_validators_and_poll_all_continues(tmp_path):
    fetch = Mock(side_effect=[
        _fetched(_rss(("one", "Fold a Boat", 700)), ETag='"stable"'),
        _fetched(b"invalid", ETag='"invalid"'),
        _fetched(_rss(("two", "Fold a Duck", 700))),
    ])
    store = SubscriptionStore(tmp_path, fetch=fetch, clock=Clock())
    sub = store.create({"url": PODCAST})
    store.create({"url": PODCAST + "?second"})
    store.poll(sub["id"])
    first, second = store.poll_all()
    assert first["error"] == "Invalid feed XML."
    assert store.get(sub["id"])["etag"] == '"stable"'
    assert second["new_count"] == 1
    assert len(store.list_releases()) == 2


def test_overlapping_polls_serialize_discovery_and_ingestion(tmp_path):
    entered, release, started = threading.Event(), threading.Event(), threading.Event()
    clock = Clock()
    fetch = Mock(return_value=_fetched(_rss(("fold", "Fold a Boat", 800))))

    def wait_then_ingest(*args):
        entered.set()
        assert release.wait(5)
        return _success(*args)

    ingest = Mock(side_effect=wait_then_ingest)
    first = SubscriptionStore(tmp_path, fetch=fetch, ingest_podcast=ingest, clock=clock)
    second = SubscriptionStore(tmp_path, fetch=fetch, ingest_podcast=ingest, clock=clock)
    sub = first.create({"url": PODCAST, "auto_ingest": True})

    def poll_second():
        started.set()
        return second.poll(sub["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(first.poll, sub["id"])
        try:
            assert entered.wait(5)
            later = pool.submit(poll_second)
            assert started.wait(5)
            assert second.list_releases()[0]["status"] == "pending"
        finally:
            release.set()
        assert active.result(timeout=5)["attempted_count"] == 1
        assert later.result(timeout=5)["attempted_count"] == 0
    ingest.assert_called_once()


def test_poll_and_crud_routes_enforce_browser_boundary(tmp_path, monkeypatch):
    fetch = Mock(side_effect=[_fetched(_rss(("fold", "Fold a Boat", 800))), FeedPollError("offline")])
    store = SubscriptionStore(tmp_path, fetch=fetch, clock=Clock())
    monkeypatch.setattr(local_api, "SERVICE", SimpleNamespace(subscription_store=lambda: store))

    def call(method, path, body=None, status=200, **options):
        actual, _, raw = _request(method, path, body, **options)
        assert actual == status, raw
        return json.loads(raw)

    path = "/v1/subscriptions"
    call("POST", path, {"url": PODCAST}, 403, authenticated=False)
    call("POST", path, {"url": PODCAST, "title_include": "["}, 400)
    sub = call("POST", path, {"url": PODCAST}, 201)["subscription"]
    sub_id = sub["id"]
    assert call("GET", path)["subscriptions"] == [sub]
    assert call("POST", path, {"id": sub_id, "min_duration_s": 0})["subscription"]["min_duration_s"] == 0
    call("POST", path, {"id": sub_id, "url": PODCAST + "/changed"}, 400)
    call("POST", path + "/poll-all", {}, 403, authenticated=False)
    call("POST", path + "/missing/poll", {}, 404)
    call("POST", path + f"/{sub_id}/poll", {}, 403, authenticated=False)
    assert call("POST", path + f"/{sub_id}/poll", {})["poll"]["new_count"] == 1
    assert call("POST", path + "/poll-all", {})["polls"][0]["error"] == "offline"
    fetch.side_effect = FeedPollError("offline")
    call("POST", path + f"/{sub_id}/poll", {}, 502)
    call("GET", "/v1/releases?since=", status=400)
    assert call("GET", "/v1/releases?since=2026-11-18T12%3A00%3A00Z")["releases"]
    call("DELETE", path + f"/{sub_id}", {}, 403, authenticated=False)
    call("DELETE", path + f"/{sub_id}", {})
    call("DELETE", path + f"/{sub_id}", {}, 404)
    assert call("GET", path)["subscriptions"] == []
    assert len(call("GET", "/v1/releases")["releases"]) == 1


def test_podcast_duration_comes_from_stt_and_short_source_leaves_no_artifacts(audio_app, monkeypatch):
    app = audio_app
    app.service.runtime_data_dir = app.root / "runtime"
    fetch = Mock(side_effect=[
        _fetched(_rss(("short", "Fold a tiny Boat", 900))),
        _fetched(_rss(("long", "Fold a large Boat", 70))),
    ])
    monkeypatch.setattr(subscriptions.feeds, "safe_fetch", fetch)
    store = app.service.subscription_store()
    sub = store.create({"url": PODCAST, "auto_ingest": True})

    (short,) = store.poll(sub["id"])["releases"]
    assert (short["status"], short["reason"], short["duration_seconds"]) == (
        "skipped", "min_duration_s", 3,
    )
    assert short["source_id"] not in app.library.videos
    assert not list((app.library.data_dir / "media").iterdir())
    assert not list(app.library.data_dir.glob("library/videos/*"))

    app.engine.result = TranscriptionResult([SEGMENT], "en", 750, "fake", "fixture", True)
    (long,) = store.poll(sub["id"])["releases"]
    assert long["status"] == "ingested" and long["duration_seconds"] == 750
    source_id = make_rss_source_id(PODCAST, "long")
    assert long["source_id"] == source_id
    reloaded = video_library.VideoLibrary(data_dir=app.library.data_dir, processor=app.library.processor)
    source = reloaded.videos[source_id]["source"]
    assert source["published_at"] == PUBLISHED
    assert source["stt"]["audio_duration_seconds"] == 750
    app.feed.assert_not_called()


def _youtube_service(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_RAG_FORCE_HASH_EMBEDDINGS", "1")
    service = _make_service()
    service.runtime_data_dir = tmp_path / "runtime"
    library = video_library.VideoLibrary(data_dir=tmp_path / "data")
    service.engine = SimpleNamespace(library=library)
    service.lock = threading.Lock()
    service.jobs = {}
    monkeypatch.setattr(service, "_hydrate_video_title", Mock())
    monkeypatch.setattr(library, "backfill_source_metadata", Mock())
    return service, library


def test_youtube_single_ingest_persists_date_and_removes_new_short_source(tmp_path, monkeypatch):
    service, library = _youtube_service(tmp_path, monkeypatch)
    fetch = Mock(return_value=_fetched(_atom("synthetic00", "synthetic01"), url=YOUTUBE))
    monkeypatch.setattr(subscriptions.feeds, "safe_fetch", fetch)
    transcript = Mock(side_effect=lambda video_id, *args, **kwargs: [
        {"start": 0 if video_id.endswith("0") else 745, "duration": 5, "text": "Fold a paper boat."}
    ])
    monkeypatch.setattr(library, "_fetch_transcript", transcript)
    store = service.subscription_store()
    store.pause = Mock()
    sub = store.create({"url": YOUTUBE, "auto_ingest": True})
    short, long = store.poll(sub["id"])["releases"]
    assert (short["status"], short["reason"], short["duration_seconds"]) == ("skipped", "min_duration_s", 5)
    assert long["status"] == "ingested" and long["duration_seconds"] == 750
    assert all(job.mode == "single" for job in service.jobs.values())
    assert all(call.kwargs["stop_on_throttle"] for call in transcript.call_args_list)
    reloaded = video_library.VideoLibrary(data_dir=library.data_dir, processor=library.processor)
    assert list(reloaded.videos) == ["synthetic01"]
    assert reloaded.videos["synthetic01"]["source"]["published_at"] == PUBLISHED


@pytest.mark.parametrize("error, reason, status", [
    (IpBlocked("synthetic00"), "rate_limited", "pending"),
    (RequestBlocked("synthetic00"), "request_blocked", "failed"),
])
def test_youtube_provider_throttle_stops_before_fallback(tmp_path, monkeypatch, error, reason, status):
    service, library = _youtube_service(tmp_path, monkeypatch)
    primary = Mock(side_effect=error)
    fallback = Mock(side_effect=AssertionError("Throttled requests must stop before fallback"))
    monkeypatch.setattr(video_library, "YouTubeTranscriptApi", lambda: SimpleNamespace(fetch=primary))
    monkeypatch.setattr(library, "_fetch_transcript_from_watch_page", fallback)
    monkeypatch.setattr(subscriptions.feeds, "safe_fetch", Mock(
        return_value=_fetched(_atom("synthetic00", "synthetic01"), url=YOUTUBE)
    ))
    store = service.subscription_store()
    store.pause = Mock()
    sub = store.create({"url": YOUTUBE, "auto_ingest": True})
    first, second = store.poll(sub["id"])["releases"]
    assert (first["status"], first["reason"]) == (status, reason)
    assert second["status"] == "pending"
    primary.assert_called_once()
    fallback.assert_not_called()


def test_http_429_retry_after_does_not_retry_inline(tmp_path, monkeypatch):
    _, library = _youtube_service(tmp_path, monkeypatch)
    headers = Message()
    headers["Retry-After"] = "105"
    opener = Mock(side_effect=HTTPError("https://www.youtube.com/watch", 429, "Slow down", headers, None))
    monkeypatch.setattr(video_library, "urlopen", opener)
    with pytest.raises(video_library.TranscriptRequestPaused) as caught:
        library._open_with_retry(None, stop_on_throttle=True)
    assert caught.value.reason == "rate_limited"
    assert caught.value.retry_after == 105
    opener.assert_called_once()


def test_cli_list_and_dry_run_without_library_startup(tmp_path, monkeypatch, capsys):
    fetch = Mock(return_value=_fetched(_rss(("fold", "Fold a Boat", 800))))
    store = SubscriptionStore(tmp_path, fetch=fetch, clock=Clock())
    sub = store.create({"url": PODCAST, "auto_ingest": True})
    monkeypatch.setattr(subscriptions, "SubscriptionStore", lambda: store)
    assert subscriptions.main(["list"]) == 0
    assert json.loads(capsys.readouterr().out) == [sub]
    assert subscriptions.main(["poll", "--sub", sub["id"], "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["attempted_count"] == 0
    assert not store.releases_path.exists()


def test_server_writer_lease_blocks_a_second_live_writer(tmp_path):
    with subscriptions.library_writer(tmp_path):
        with pytest.raises(RuntimeError, match="library is in use"):
            with subscriptions.library_writer(tmp_path):
                pytest.fail("Acquired a second library writer")
    with subscriptions.library_writer(tmp_path):
        assert (tmp_path / "subscription-library.lock").stat().st_mode & 0o777 == 0o600


def test_periodic_poller_uses_interval_and_defaults_to_off(monkeypatch):
    event = Mock()
    event.wait.side_effect = [False, True]
    thread = Mock()
    store = Mock()
    store.poll_all.return_value = []
    monkeypatch.setattr(subscriptions, "threading", SimpleNamespace(
        Event=lambda: event, Thread=thread, TIMEOUT_MAX=threading.TIMEOUT_MAX,
    ))
    assert subscriptions.start_poller(lambda: store, "0") == event
    thread.assert_not_called()
    assert subscriptions.start_poller(lambda: store, "2.5") == event
    thread.return_value.start.assert_called_once()
    thread.call_args.kwargs["target"]()
    assert [call.args for call in event.wait.call_args_list] == [(150,), (150,)]
    store.poll_all.assert_called_once()
