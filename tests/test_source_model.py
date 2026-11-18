"""Source metadata and playback links use synthetic data only."""

import hashlib
import json

import pytest

from multilingual.source_model import (
    SOURCE_PLATFORMS,
    canonical_url,
    is_youtube,
    make_file_source_id,
    make_rss_source_id,
    normalize_source,
    timestamp_url,
)
from multilingual.youtube_metadata import normalize_youtube_source


@pytest.mark.parametrize("raw", [
    None,
    {"url": "https://youtu.be/fixtureEN01", "metadata_provider": "fallback"},
    {
        "platform": "youtube",
        "channel": {"id": "UC-fixture", "name": "Porcelain Workshop"},
        "metadata_provider": "youtube_data_api",
        "fetched_at": "2026-01-01T00:00:00Z",
    },
])
@pytest.mark.parametrize("legacy", [False, True])
def test_youtube_normalization_preserves_existing_bytes(raw, legacy):
    source = normalize_source("fixtureEN01", raw, legacy=legacy)
    assert is_youtube(source)
    assert json.dumps(source) == json.dumps(
        normalize_youtube_source("fixtureEN01", raw, legacy=legacy)
    )


def test_youtube_playback_and_optional_publication_date():
    date = "2026-01-01T00:00:00Z"
    source = normalize_source("fixtureEN01", {"published_at": date})
    assert source["published_at"] == date
    assert canonical_url(source) == "https://www.youtube.com/watch?v=fixtureEN01"
    assert timestamp_url(source, 18.75) == "https://www.youtube.com/watch?v=fixtureEN01&t=18s"


def test_source_ids_are_stable_and_feed_scoped():
    feed = "https://radio.example/feed.xml"
    guid = "kiln-7"
    digest = hashlib.sha256(f"{feed}\n{guid}".encode()).hexdigest()
    assert make_rss_source_id(feed, guid) == f"pod_{digest[:16]}"
    assert make_rss_source_id(feed + "/other", guid) != make_rss_source_id(feed, guid)
    assert make_rss_source_id(feed, "kiln-8") != make_rss_source_id(feed, guid)
    assert make_file_source_id(digest) == f"aud_{digest[:16]}"


def test_audio_metadata_and_playback_urls():
    assert SOURCE_PLATFORMS == {"youtube", "podcast_rss", "local_audio"}
    media_url = "https://radio.example/kiln.mp3?format=mp3"
    date = "2026-01-01T00:00:00Z"
    source_id = "pod_0123456789abcdef"
    source = normalize_source(source_id, {
        "platform": "podcast_rss",
        "video_id": "ignored",
        "url": "https://radio.example/kiln",
        "media_url": media_url,
        "show": {"id": "craft", "name": " Craft radio ", "url": "https://radio.example"},
        "episode": {"guid": "kiln-7", "duration_seconds": "90.25"},
        "published_at": date,
        "transcript_provider": "feed",
        "stt": {"engine": "synthetic", "word_timestamps": True},
        "metadata_provider": "rss",
        "fetched_at": date,
    })
    assert source == {
        "platform": "podcast_rss",
        "video_id": source_id,
        "url": "https://radio.example/kiln",
        "media_url": media_url,
        "show": {"id": "craft", "name": "Craft radio", "url": "https://radio.example"},
        "episode": {"guid": "kiln-7", "published_at": date, "duration_seconds": 90.25},
        "published_at": date,
        "transcript_provider": "feed",
        "stt": {
            "engine": "synthetic", "model": None, "language": None, "audio_sha256": None,
            "created_at": None, "word_timestamps": True,
        },
        "local_media_path": None,
        "metadata_provider": "rss",
        "fetched_at": date,
    }
    assert not is_youtube(source)
    assert normalize_source(source_id, source) == source
    assert canonical_url(source) == source["url"]
    assert timestamp_url(source, 8.8) == media_url + "#t=8"
    assert timestamp_url({**source, "local_media_path": "/tmp/kiln.mp3"}, 8.8) == (
        f"/v1/sources/{source_id}/media#t=8"
    )
    audio_id = "aud_fedcba9876543210"
    audio = normalize_source(audio_id, {
        "platform": "local_audio",
        "url": "javascript:alert(1)",
        "local_media_path": "/tmp/kiln.mp3",
    })
    assert audio["url"] is None
    assert canonical_url(audio) == f"/v1/sources/{audio_id}/media"
    assert timestamp_url(audio, 0) == f"/v1/sources/{audio_id}/media#t=0"
    assert timestamp_url({**audio, "local_media_path": None}, 0) == ""
