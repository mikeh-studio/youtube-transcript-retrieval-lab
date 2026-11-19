"""Source provenance and playback links shared by the local library."""

import hashlib
import math
import re
from urllib.parse import quote, urlsplit

from multilingual.youtube_metadata import (
    canonical_youtube_url,
    normalize_youtube_source,
)


SOURCE_PLATFORMS = {"youtube", "podcast_rss", "local_audio"}
SOURCE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _text(value):
    return str(value or "").strip() or None


def _fields(value, keys):
    raw = value if isinstance(value, dict) else {}
    return {key: _text(raw.get(key)) for key in keys}


def _web_url(value):
    url = _text(value)
    try:
        parsed = urlsplit(url or "")
    except ValueError:
        return None
    return url if parsed.scheme in {"https", "http"} and parsed.netloc else None


def _platform(record):
    platform = _text(record.get("platform")) if isinstance(record, dict) else None
    return (platform or "youtube").lower()


def is_youtube(record):
    """Identify YouTube provenance, including records from before platform tags."""
    return _platform(record) == "youtube"


def normalize_source(source_id, raw=None, *, legacy=False):
    """Normalize platform-specific provenance under the library's source ID."""
    raw = raw if isinstance(raw, dict) else {}
    platform = _platform(raw)
    published_at = _text(raw.get("published_at"))
    if platform == "youtube":
        normalized = normalize_youtube_source(source_id, raw, legacy=legacy)
    elif platform in SOURCE_PLATFORMS:
        show = _fields(raw.get("show"), ("id", "name", "url"))
        show["url"] = _web_url(show["url"])
        raw_episode = raw.get("episode")
        raw_episode = raw_episode if isinstance(raw_episode, dict) else {}
        try:
            duration = float(raw_episode.get("duration_seconds"))
            if not math.isfinite(duration) or duration < 0:
                duration = None
        except (TypeError, ValueError, OverflowError):
            duration = None
        raw_stt = raw.get("stt")
        raw_stt = raw_stt if isinstance(raw_stt, dict) else {}
        stt = _fields(
            raw_stt, ("engine", "model", "language", "audio_sha256", "created_at")
        )
        stt["word_timestamps"] = raw_stt.get("word_timestamps") is True
        normalized = {
            "platform": platform,
            "video_id": str(source_id or "").strip(),
            "url": _web_url(raw.get("url")),
            "media_url": _web_url(raw.get("media_url")),
            "show": show,
            "episode": {
                "guid": _text(raw_episode.get("guid")),
                "published_at": _text(raw_episode.get("published_at")) or published_at,
                "duration_seconds": duration,
            },
            "transcript_provider": _text(raw.get("transcript_provider")),
            "stt": stt,
            "local_media_path": _text(raw.get("local_media_path")),
            "metadata_provider": _text(raw.get("metadata_provider")),
            "fetched_at": _text(raw.get("fetched_at")),
        }
    else:
        raise ValueError(f"Unsupported source platform: {platform}")
    if published_at:
        normalized["published_at"] = published_at
    return normalized


def _audio_playback_url(record):
    if record.get("local_media_path"):
        return f"/v1/sources/{quote(str(record['video_id']), safe='')}/media"
    return record.get("media_url") or ""


def canonical_url(record):
    """Return the source page or the available audio locator."""
    if is_youtube(record):
        return canonical_youtube_url(record["video_id"])
    return record.get("url") or _audio_playback_url(record)


def playback_target(record):
    """Return the platform's base playback URL, preferring retained audio."""
    if is_youtube(record):
        return {"kind": "youtube", "url": canonical_youtube_url(record["video_id"])}
    media_url = _audio_playback_url(record)
    return {"kind": "media", "url": media_url} if media_url else None


def timestamp_url(record, seconds):
    """Link to the whole-second offset on the platform's playback surface."""
    start = max(0, int(float(seconds)))
    target = playback_target(record)
    if target is None:
        return ""
    url = target["url"]
    if target["kind"] == "youtube":
        return f"{url}&t={start}s"
    return f"{url.split('#', 1)[0]}#t={start}"


def make_rss_source_id(feed_url, guid):
    """Derive an episode ID scoped to its feed."""
    return "pod_" + hashlib.sha256(f"{feed_url}\n{guid}".encode("utf-8")).hexdigest()[:16]


def make_file_source_id(sha256):
    """Derive a local audio ID from its content digest."""
    return "aud_" + sha256[:16]
