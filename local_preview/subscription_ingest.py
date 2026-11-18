"""Connect subscription releases to the existing library ingestion paths."""

from multilingual.source_model import make_rss_source_id
from multilingual.video_library import TranscriptRequestPaused
from multilingual.youtube_metadata import canonical_youtube_url


class SubscriptionSourceSkipped(Exception):
    """Reject a new record before it is added to the searchable library."""

    def __init__(self, outcome):
        super().__init__(outcome["reason"])
        self.outcome = outcome


def _source_outcome(record, subscription, source_id, *, existing, duration=None):
    if duration is None:
        duration = max(
            (segment["end"] for segment in (record.get("full_transcript") or {}).get("segments", [])),
            default=None,
        )
    minimum = subscription["min_duration_s"]
    reason = None
    if minimum:
        if duration is None:
            reason = "duration_unavailable"
        elif duration < minimum:
            reason = "min_duration_s"
    return {
        "status": "skipped" if reason else "ingested",
        "reason": reason or ("already_indexed" if existing else None),
        "source_id": source_id,
        "duration_seconds": duration,
    }


def _apply_publication_date(library, record, item):
    date = item["published_at"]
    source = record["source"]
    if date and date != source.get("published_at"):
        previous = source.get("published_at")
        source["published_at"] = date
        try:
            library.save()
        except Exception:
            if previous is None:
                source.pop("published_at")
            else:
                source["published_at"] = previous
            raise


def ingest_youtube(subscription, item, _show, *, library, write_lock, ingest):
    """Apply the release's duration and publication date to a single YouTube source."""
    source_id = item["external_id"]

    def prepare_record(record):
        outcome = _source_outcome(record, subscription, source_id, existing=False)
        if outcome["status"] == "skipped":
            raise SubscriptionSourceSkipped(outcome)
        if item["published_at"]:
            record["source"]["published_at"] = item["published_at"]

    with write_lock:
        existing = source_id in library.videos
        if not existing:
            try:
                result = ingest(
                    url=canonical_youtube_url(source_id), mode="single",
                    language=subscription["language"], force=False, stop_on_throttle=True,
                    prepare_record=prepare_record,
                )
            except SubscriptionSourceSkipped as exc:
                return exc.outcome
            except TranscriptRequestPaused as exc:
                return {
                    "status": "pending" if exc.reason == "rate_limited" else "failed",
                    "reason": exc.reason,
                    "retry_after": exc.retry_after,
                }
            if any(job["status"] == "failed" for job in result["jobs"]):
                return {"status": "failed", "reason": "ingest_failed", "source_id": source_id}
        record = library.videos.get(source_id)
        if record is None:
            return {"status": "failed", "reason": "source_missing", "source_id": source_id}
        outcome = _source_outcome(record, subscription, source_id, existing=existing)
        if existing and outcome["status"] == "ingested":
            _apply_publication_date(library, record, item)
        return outcome


def ingest_podcast(subscription, item, show, *, library, write_lock, get_queue, get_job):
    """Submit a persisted enclosure to the audio worker and read its final status."""
    source_id = make_rss_source_id(subscription["url"], item["external_id"])
    with write_lock:
        existing = source_id in library.videos
    if not existing:
        try:
            job_id = get_queue().submit_episode_and_wait(
                {
                    "kind": "rss_episode", "feed_url": subscription["url"],
                    "guid": item["external_id"], "language": subscription["language"],
                },
                feed_snapshot=(show, item), min_duration_s=subscription["min_duration_s"],
            )
        except ValueError:
            return {"status": "failed", "reason": "ingest_failed", "source_id": source_id}
        job = get_job(job_id)
        if job.status == "skipped":
            return {
                "status": "skipped", "reason": "min_duration_s", "source_id": source_id,
                "duration_seconds": job.audio_duration_seconds,
            }
        if job.status != "completed":
            return {"status": "failed", "reason": "ingest_failed", "source_id": source_id}
        existing = job.transcription_seconds is None
    with write_lock:
        record = library.videos.get(source_id)
        if record is None:
            return {"status": "failed", "reason": "source_missing", "source_id": source_id}
        duration = record["source"].get("stt", {}).get("audio_duration_seconds")
        outcome = _source_outcome(record, subscription, source_id, existing=existing, duration=duration)
        if outcome["status"] == "ingested":
            _apply_publication_date(library, record, item)
        return outcome
