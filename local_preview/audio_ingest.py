"""Single-worker ingestion of podcast enclosures and user-provided audio files."""

import hashlib
import os
import queue
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from urllib.parse import unquote, urlsplit

from local_preview.audio_media import AUDIO_TYPES, VIDEO_TYPES, registered_media_path
from local_preview.feeds import is_youtube_url, open_fetch, preview_feed, split_http_url
from local_preview.local_files import json_bytes, write_atomic
from local_preview.stt.base import engine_name, get_engine
from multilingual.source_model import canonical_url, make_file_source_id, make_rss_source_id

BLOCK_BYTES = 65536
MEDIA_ACCEPT = "audio/*, video/*, application/octet-stream"
SOURCE_TYPES = {"rss_episode": "podcast_rss", "local_file": "local_audio"}
MIME_EXTENSIONS = {
    **{mime: ext for ext, mime in VIDEO_TYPES.items()},
    **{mime: ext for ext, mime in AUDIO_TYPES.items()},
    "audio/ogg": ".ogg",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
    "application/ogg": ".ogg",
}


@dataclass(frozen=True)
class IngestJob:
    job_id: str
    video_id: str
    url: str
    language: str
    mode: str
    status: str
    attempts: int
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    source_type: str = "youtube"
    step: str = ""
    progress: float = 0.0
    transcription_seconds: float | None = None
    audio_duration_seconds: float | None = None
    stt_engine: str | None = None
    stt_model: str | None = None


def _reject_youtube_media(url):
    if is_youtube_url(url):
        raise ValueError("YouTube audio downloads are unsupported.")


def _media_extension(url, medium):
    content_type = (medium or "").split(";", 1)[0].strip().lower()
    extension = Path(unquote(urlsplit(url).path)).suffix.lower()
    if extension in AUDIO_TYPES or extension in VIDEO_TYPES:
        return extension
    return MIME_EXTENSIONS.get(content_type)


def _response_content_type(response):
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
    if content_type and not (
        content_type.startswith(("audio/", "video/"))
        or content_type in {"application/ogg", "application/octet-stream", "binary/octet-stream"}
    ):
        raise ValueError("The enclosure did not return audio or video.")
    return content_type


def _local_path(root, raw):
    if not isinstance(raw, str) or not raw.strip() or urlsplit(raw).scheme:
        raise ValueError("path must name a local audio or video file.")
    path = Path(raw.strip()).expanduser()
    try:
        path = (root / path).resolve()
        if path.is_file() and (path.suffix.lower() in AUDIO_TYPES or path.suffix.lower() in VIDEO_TYPES):
            return path
    except (OSError, RuntimeError):
        pass
    raise ValueError("path must name an existing supported local audio or video file.")


def _copy_and_hash(blocks, destination, max_bytes):
    digest = hashlib.sha256()
    size = 0
    for block in blocks:
        size += len(block)
        if size > max_bytes:
            raise ValueError("Audio exceeds YT_RAG_AUDIO_MAX_MB.")
        digest.update(block)
        destination.write(block)
    if not size:
        raise ValueError("Audio file is empty.")
    return digest.hexdigest()


@contextmanager
def _restore_on_error(paths, *, backup_dir):
    """Preserve the previous files if publishing a replacement raises."""
    with tempfile.TemporaryDirectory(dir=backup_dir) as directory:
        backups = {}
        for index, path in enumerate(paths):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("An audio artifact destination is not a regular file.")
            if path.is_file():
                backup = Path(directory) / str(index)
                os.link(path, backup, follow_symlinks=False)
                backups[path] = backup
        try:
            yield
        except Exception:
            for path in paths:
                if backup := backups.get(path):
                    if not path.exists() or not path.samefile(backup):
                        backup.replace(path)
                else:
                    path.unlink(missing_ok=True)
            raise


class AudioIngestQueue:
    def __init__(
        self, *, library, write_lock, root_dir, clock, store_job, update_job,
        mark_router_stale, engine_factory=get_engine, fetch_feed=preview_feed,
        stream_media=open_fetch, timer=monotonic,
    ):
        self.library = library
        self.write_lock = write_lock
        self.root_dir = Path(root_dir)
        self.clock = clock
        self.store_job = store_job
        self.update_job = update_job
        self.mark_router_stale = mark_router_stale
        self.engine_factory = engine_factory
        self.fetch_feed = fetch_feed
        self.stream_media = stream_media
        self.timer = timer
        self.media_dir = Path(library.data_dir).resolve() / "media"
        self.pending = queue.Queue()
        threading.Thread(target=self._work, name="audio-ingest", daemon=True).start()

    def submit(self, payload):
        """Validate an audio request and enqueue it without waiting for transcription."""
        kind = payload.get("kind")
        language = payload.get("language")
        engine = engine_name(payload.get("engine"))
        force = payload.get("force", False)
        if kind not in ("rss_episode", "local_file"):
            raise ValueError("kind must be rss_episode or local_file.")
        if language not in ("ja", "en"):
            raise ValueError("language must be ja or en.")
        if not isinstance(force, bool):
            raise ValueError("force must be a boolean.")

        request = {"force": force, "engine": engine}
        source_id, url = "", ""
        if kind == "rss_episode":
            url, guid = payload.get("feed_url"), payload.get("guid")
            split_http_url(url)
            if not isinstance(guid, str) or not guid.strip():
                raise ValueError("guid is required for an RSS episode.")
            source_id = make_rss_source_id(url, guid)
            request.update(feed_url=url, guid=guid)
        else:
            raw = payload.get("path")
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("path is required for a local file.")
            request["path"] = raw

        now = self.clock()
        job = IngestJob(
            job_id=f"job_{uuid.uuid4()}", video_id=source_id, url=url,
            language=language, mode=kind, status="queued", attempts=0,
            error_code=None, error_message=None, created_at=now, updated_at=now,
            source_type=SOURCE_TYPES[kind], step="queued",
        )
        self.store_job(job)
        self.pending.put((job, request))
        return job

    def _work(self):
        while True:
            job, request = self.pending.get()
            try:
                self._ingest(job, request)
            except Exception as exc:
                self.update_job(
                    job.job_id, status="failed", error_code="AUDIO_INGEST_FAILED",
                    error_message=str(exc)[:500],
                )
            finally:
                self.pending.task_done()

    def _already_indexed(self, source_id, force):
        if force:
            return None
        with self.write_lock:
            return self.library.videos.get(source_id)

    def _complete(self, job_id, source_id, record, **timing):
        source = record["source"]
        self.update_job(
            job_id, status="completed", step="completed", progress=1.0,
            video_id=source_id, url=canonical_url(source), language=record["language"],
            **timing,
        )

    def _ingest(self, job, request):
        self.update_job(job.job_id, status="running", step="resolving", attempts=1, progress=0.05)
        source_id = job.video_id
        if source_id and (existing := self._already_indexed(source_id, request["force"])):
            self._complete(job.job_id, source_id, existing)
            return

        try:
            max_bytes = int(os.environ.get("YT_RAG_AUDIO_MAX_MB", "500")) * 1024 * 1024
        except ValueError as exc:
            raise ValueError("YT_RAG_AUDIO_MAX_MB must be a positive integer.") from exc
        if max_bytes <= 0:
            raise ValueError("YT_RAG_AUDIO_MAX_MB must be a positive integer.")
        if self.media_dir.is_symlink():
            raise ValueError("The media directory must be inside the data directory.")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        staged = None
        metadata = {"platform": job.source_type}
        try:
            if job.mode == "rss_episode":
                feed_url, guid = request["feed_url"], request["guid"]
                feed = self.fetch_feed(feed_url)
                item = next((row for row in feed["items"] if row["external_id"] == guid), None)
                if item is None or not item["media_url"]:
                    raise ValueError("The feed does not contain an audio enclosure for this GUID.")
                self.update_job(job.job_id, step="downloading", progress=0.15)
                media_url = item["media_url"]
                _reject_youtube_media(split_http_url(media_url))
                if (item.get("media_length_bytes") or 0) > max_bytes:
                    raise ValueError("Audio exceeds YT_RAG_AUDIO_MAX_MB.")
                with self.stream_media(
                    media_url, max_bytes=max_bytes, timeout=30, accept=MEDIA_ACCEPT,
                    check_url=_reject_youtube_media,
                ) as response:
                    content_type = _response_content_type(response)
                    extension = (
                        _media_extension(response.url, content_type)
                        or _media_extension(media_url, item["media_type"])
                    )
                    if extension is None:
                        raise ValueError("Unsupported podcast enclosure format.")
                    staged = self.media_dir / f"{source_id}{extension}.part"
                    staged.unlink(missing_ok=True)
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    with os.fdopen(os.open(staged, flags, 0o600), "wb") as output:
                        digest = _copy_and_hash(response.iter_bytes(), output, max_bytes)
                title = item["title"] or guid
                metadata.update(
                    url=item["link"], media_url=media_url,
                    show={"id": feed_url, "name": feed["title"], "url": feed["link"]},
                    episode={"guid": guid, "duration_seconds": item["duration_seconds"]},
                    published_at=item["published_at"], metadata_provider="rss",
                    fetched_at=self.clock(),
                )
            else:
                self.update_job(job.job_id, step="validating", progress=0.15)
                path = _local_path(self.root_dir, request["path"])
                extension, title = path.suffix.lower(), path.stem
                with path.open("rb") as source, tempfile.NamedTemporaryFile(
                    dir=self.media_dir, suffix=f"{extension}.part", delete=False
                ) as output:
                    staged = Path(output.name)
                    blocks = iter(lambda: source.read(BLOCK_BYTES), b"")
                    digest = _copy_and_hash(blocks, output, max_bytes)
                source_id = make_file_source_id(digest)
                if existing := self._already_indexed(source_id, request["force"]):
                    staged.unlink()
                    self._complete(job.job_id, source_id, existing)
                    return

            self.update_job(job.job_id, video_id=source_id, step="transcribing", progress=0.4)
            started = self.timer()
            result = self.engine_factory(request["engine"]).transcribe(staged, job.language)
            elapsed = self.timer() - started
            if not result.segments:
                raise ValueError("Speech-to-text returned no transcript segments.")
            if metadata.get("episode", {}).get("duration_seconds") is None:
                metadata.setdefault("episode", {})["duration_seconds"] = result.duration
            metadata.update(
                transcript_provider=result.engine,
                stt={
                    "engine": result.engine, "model": result.model, "language": result.language,
                    "audio_sha256": digest, "created_at": self.clock(),
                    "word_timestamps": result.word_timestamps,
                },
            )
            self.update_job(job.job_id, step="indexing", progress=0.85)
            with self.write_lock:
                record = self._publish(source_id, staged, extension, title, metadata, result)
                staged.unlink(missing_ok=True)
                self._complete(
                    job.job_id, source_id, record, transcription_seconds=elapsed,
                    audio_duration_seconds=result.duration, stt_engine=result.engine,
                    stt_model=result.model,
                )
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    def _publish(self, source_id, staged, extension, title, metadata, result):
        keep_audio = os.environ.get("YT_RAG_KEEP_AUDIO", "1") == "1"
        media = self.media_dir / f"{source_id}{extension}"
        previous_media = registered_media_path(self.library, source_id)
        if previous_media and (
            previous_media.parent != self.media_dir or previous_media.stem != source_id
        ):
            previous_media = None
        record_path = Path(self.library._video_record_path(source_id))
        words = record_path.with_name("stt_words.json")
        segments = sorted(result.segments, key=lambda row: row.start)
        word_payload = None
        if result.word_timestamps:
            word_payload = json_bytes({
                "version": 1,
                "segments": [
                    {"start": row.start, "end": row.end, "words": row.words or []}
                    for row in segments
                ],
            })
        affected = [media, words] if keep_audio else [words]
        if previous_media and (not keep_audio or previous_media != media):
            affected.append(previous_media)
        # Library save publishes the source record, shared index and manifest separately.
        affected.extend([
            record_path, Path(self.library._index_path()), Path(self.library._manifest_path()),
        ])
        with _restore_on_error(affected, backup_dir=self.media_dir):
            if keep_audio:
                staged.replace(media)
                metadata["local_media_path"] = f"media/{media.name}"
            if word_payload is None:
                words.unlink(missing_ok=True)
            else:
                write_atomic(words, word_payload)
            if previous_media and (not keep_audio or previous_media != media):
                previous_media.unlink()
            self.library.add_transcribed_source(
                source_id, [
                    {"start": row.start, "end": row.end, "text": row.text}
                    for row in segments
                ],
                title=title, source=metadata, language=result.language, persist=True,
            )
        self.mark_router_stale()
        return self.library.videos[source_id]
