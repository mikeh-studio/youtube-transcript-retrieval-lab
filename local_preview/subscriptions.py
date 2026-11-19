"""Durable feed subscriptions and release polling."""

import argparse
import json
import logging
import math
import os
import re
import sys
import threading
import uuid
from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep

from local_preview import feeds
from local_preview.local_files import json_bytes, write_atomic
from local_preview.subscription_patterns import compile_title_pattern
from multilingual.youtube_metadata import YOUTUBE_INGEST_PAUSE_SECONDS

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "data" / "runtime"
MAX_AUTO_INGESTS = 5
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
EDITABLE = {
    "url", "kind", "language", "auto_ingest", "title_include",
    "title_exclude", "min_duration_s", "domain_tag",
}
TITLE_FILTERS = ("title_include", "title_exclude")


class SubscriptionNotFound(KeyError):
    """The requested subscription ID does not exist."""


class FeedPollError(RuntimeError):
    """A feed could not be fetched or parsed."""


@contextmanager
def _file_lock(path, *, blocking=True):
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise RuntimeError(
                "The library is in use. Poll through the running server or stop it before using the CLI."
            ) from exc
        yield


def library_writer(runtime_dir=RUNTIME_DIR):
    """Keep the server and a standalone live poll from writing separate library snapshots."""
    return _file_lock(Path(runtime_dir) / "subscription-library.lock", blocking=False)


def _utc_date(value):
    try:
        date = datetime.fromisoformat(value)
        if date.tzinfo is None:
            raise ValueError
        return date.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("since must be an ISO 8601 timestamp with a timezone.") from exc


def _subscription_fields(payload, previous=None):
    unknown = payload.keys() - EDITABLE
    if unknown:
        raise ValueError(f"Unknown subscription field: {min(unknown)}")
    fields = {
        "language": "en", "auto_ingest": False, "title_include": None,
        "title_exclude": None, "min_duration_s": 600, "domain_tag": None,
    }
    if previous:
        fields.update({key: previous[key] for key in EDITABLE if key in previous})
    fields.update(payload)
    url = feeds.split_http_url(fields.get("url"))
    feeds.check_feed_destination(url)
    fields.setdefault("kind", "youtube" if feeds.is_youtube_url(url) else "podcast")
    if fields["kind"] not in ("youtube", "podcast"):
        raise ValueError("kind must be youtube or podcast.")
    if fields["language"] not in ("en", "ja"):
        raise ValueError("language must be en or ja.")
    tag = fields["domain_tag"]
    if tag is not None:
        if not isinstance(tag, str):
            raise ValueError("domain_tag must be a string or null.")
        fields["domain_tag"] = tag.strip() or None
    if not isinstance(fields["auto_ingest"], bool):
        raise ValueError("auto_ingest must be a boolean.")
    duration = fields["min_duration_s"]
    if type(duration) is not int or duration < 0:
        raise ValueError("min_duration_s must be a non-negative integer.")
    for key in TITLE_FILTERS:
        compile_title_pattern(key, fields[key])
    return fields


def _skip_reason(subscription, item, patterns):
    title = (item["title"] or "")[:300]
    include, exclude = patterns
    if include is not None and not include.search(title):
        return "title_include"
    if exclude is not None and exclude.search(title):
        return "title_exclude"
    if subscription["kind"] == "youtube" and not VIDEO_ID.fullmatch(item["external_id"]):
        return "invalid_video_id"
    if subscription["kind"] == "podcast" and not item["media_url"]:
        return "missing_enclosure"
    return None


def _public_release(release):
    return {key: value for key, value in release.items() if key not in ("item", "show")}


class SubscriptionStore:
    """Store feed cursors and one current release row per subscription and external ID."""

    def __init__(
        self, runtime_dir=RUNTIME_DIR, *, fetch=None, ingest_youtube=None,
        ingest_podcast=None, podcast_status=None, clock=None, pause=sleep,
    ):
        root = Path(runtime_dir)
        self.subscriptions_path = root / "subscriptions.json"
        self.releases_path = root / "releases.jsonl"
        self.lock_path = root / "subscriptions.lock"
        self.fetch = feeds.safe_fetch if fetch is None else fetch
        self.ingest_youtube = ingest_youtube
        self.ingest_podcast = ingest_podcast
        self.podcast_status = podcast_status
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.pause = pause

    def _state(self):
        try:
            return json.loads(self.subscriptions_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "subscriptions": {}, "youtube_backoff": None}

    def _save_state(self, state):
        write_atomic(self.subscriptions_path, json_bytes(state))

    def _releases(self):
        try:
            with self.releases_path.open(encoding="utf-8") as stream:
                rows = (json.loads(line) for line in stream if line.strip())
                return {(row["subscription_id"], row["external_id"]): row for row in rows}
        except FileNotFoundError:
            return {}

    def _save_releases(self, releases):
        write_atomic(self.releases_path, b"".join(map(json_bytes, releases.values())))

    @staticmethod
    def _get(state, subscription_id):
        try:
            return state["subscriptions"][subscription_id]
        except KeyError:
            raise SubscriptionNotFound(subscription_id) from None

    def list(self):
        """List subscriptions in creation order."""
        return [{"domain_tag": None, **row} for row in self._state()["subscriptions"].values()]

    def get(self, subscription_id):
        """Read a subscription by ID."""
        return self._get(self._state(), subscription_id)

    def create(self, payload):
        """Register a feed with auto-ingest disabled unless requested."""
        fields = _subscription_fields(payload)
        with _file_lock(self.lock_path):
            state = self._state()
            now = self.clock().isoformat()
            subscription = {
                **fields, "id": uuid.uuid4().hex, "created_at": now, "updated_at": now,
                "last_polled_at": None, "etag": None, "last_modified": None,
            }
            state["subscriptions"][subscription["id"]] = subscription
            self._save_state(state)
        return subscription

    def update(self, subscription_id, payload):
        """Update polling settings for the same feed identity."""
        with _file_lock(self.lock_path):
            state = self._state()
            subscription = self._get(state, subscription_id)
            fields = _subscription_fields(payload, subscription)
            if any(fields[key] != subscription[key] for key in ("url", "kind")):
                raise ValueError("Create a new subscription to change url or kind.")
            subscription.update(fields, updated_at=self.clock().isoformat())
            self._save_state(state)
        return subscription

    def delete(self, subscription_id):
        """Remove a subscription while retaining its release history and library sources."""
        with _file_lock(self.lock_path):
            state = self._state()
            self._get(state, subscription_id)
            del state["subscriptions"][subscription_id]
            self._save_state(state)
        return {"id": subscription_id}

    def list_releases(self, since=None):
        """Return release snapshots, optionally since an inclusive update timestamp."""
        cutoff = _utc_date(since) if since is not None else None
        return [
            _public_release(row) for row in self._releases().values()
            if cutoff is None or _utc_date(row["updated_at"]) >= cutoff
        ]

    def reconcile(self, *, blocking=True):
        """Refresh podcast release statuses from existing jobs without fetching feeds.

        With blocking=False, return False instead of waiting when a poll holds the lock.
        """
        with ExitStack() as stack:
            try:
                stack.enter_context(_file_lock(self.lock_path, blocking=blocking))
            except RuntimeError:
                if blocking:
                    raise
                return False
            state = self._state()
            self._reconcile_jobs(state["subscriptions"], self._releases())
            return True

    def _reconcile_jobs(self, subscriptions, releases):
        changed = []
        if self.podcast_status is None:
            return changed
        for (subscription_id, _), row in releases.items():
            subscription = subscriptions.get(subscription_id)
            if subscription is None or row["status"] != "ingesting":
                continue
            outcome = self.podcast_status(subscription, row["item"], row["job_id"])
            if outcome is not None:
                if outcome["reason"] == "job_lost":
                    outcome = {**outcome, "job_id": None}
                self._apply_outcome(row, outcome)
                changed.append(row)
        if changed:
            self._save_releases(releases)
        return changed

    def _apply_outcome(self, row, outcome):
        row.update({
            key: outcome[key]
            for key in ("status", "reason", "source_id", "duration_seconds", "job_id")
            if key in outcome
        }, updated_at=self.clock().isoformat())

    def poll(self, subscription_id, *, dry_run=False):
        """Fetch a feed and process up to five eligible releases, including deferred items."""
        with nullcontext() if dry_run else _file_lock(self.lock_path):
            state = self._state()
            subscription = self._get(state, subscription_id)
            try:
                patterns = tuple(compile_title_pattern(key, subscription.get(key)) for key in TITLE_FILTERS)
            except ValueError as exc:
                raise FeedPollError(str(exc)) from exc
            releases = self._releases()
            reconciled = [] if dry_run else self._reconcile_jobs({subscription_id: subscription}, releases)
            result, discovered = self._discover(subscription, releases, patterns)
            if not dry_run:
                if discovered:
                    self._save_releases(releases)
                if result.status != 304:
                    headers = {key.lower(): value for key, value in result.headers.items()}
                    subscription.update(
                        etag=headers.get("etag"), last_modified=headers.get("last-modified"),
                    )
                subscription["last_polled_at"] = self.clock().isoformat()
                self._save_state(state)

            rows = [
                row for (sub_id, _), row in releases.items()
                if sub_id == subscription_id
            ]
            in_flight = sum(row["status"] == "ingesting" for row in rows)
            pending = [row for row in rows if row["status"] == "pending"]
            attempted, changed = self._drain(
                state, subscription, releases, pending, dry_run, patterns, MAX_AUTO_INGESTS - in_flight,
            )
            reported = {row["external_id"]: row for row in discovered + reconciled + changed}
            backoff = state["youtube_backoff"] if subscription["kind"] == "youtube" else None
            retry_at = backoff["until"] if backoff and _utc_date(backoff["until"]) > self.clock() else None
            return {
                "subscription_id": subscription_id,
                "dry_run": dry_run,
                "not_modified": result.status == 304,
                "new_count": len(discovered),
                "attempted_count": attempted,
                "pending_count": sum(row["status"] == "pending" for row in pending),
                "ingesting_count": sum(row["status"] == "ingesting" for row in rows),
                "retry_at": retry_at,
                "releases": [_public_release(row) for row in reported.values()],
            }

    def _discover(self, subscription, releases, patterns):
        headers = {
            header: subscription[key] for header, key in (
                ("If-None-Match", "etag"), ("If-Modified-Since", "last_modified")
            ) if subscription[key]
        }
        try:
            result = self.fetch(
                subscription["url"], max_bytes=feeds.FEED_MAX_BYTES, timeout=15,
                accept=feeds.FEED_ACCEPT, headers=headers, check_url=feeds.check_feed_destination,
            )
            if result.status == 304:
                return result, []
            feed = feeds.parse_feed(result.body, base_url=result.url)
        except (ValueError, OSError) as exc:
            raise FeedPollError(str(exc)) from exc

        discovered = []
        now = self.clock().isoformat()
        show = {key: feed[key] for key in ("title", "link")}
        first_poll = subscription["last_polled_at"] is None
        created_at = _utc_date(subscription["created_at"]) if first_poll else None
        for item in feed["items"]:
            external_id = item["external_id"]
            key = subscription["id"], external_id
            if not external_id or key in releases:
                continue
            reason = _skip_reason(subscription, item, patterns)
            status = "skipped" if reason else ("pending" if subscription["auto_ingest"] else "discovered")
            if status == "pending" and first_poll and (
                not item["published_at"] or _utc_date(item["published_at"]) < created_at
            ):
                status, reason = "discovered", "backlog"
            row = {
                "subscription_id": subscription["id"],
                **{key: item[key] for key in ("external_id", "title", "published_at", "link")},
                "status": status, "reason": reason, "source_id": None, "duration_seconds": None,
                "discovered_at": now, "updated_at": now, "item": item, "show": show,
            }
            releases[key] = row
            discovered.append(row)
        return result, discovered

    def _drain(self, state, subscription, releases, pending, dry_run, patterns, capacity):
        attempted, changed = 0, []
        if dry_run or not subscription["auto_ingest"]:
            return attempted, changed

        youtube = subscription["kind"] == "youtube"
        ingest = self.ingest_youtube if youtube else self.ingest_podcast
        for row in pending:
            reason = _skip_reason(subscription, row["item"], patterns)
            if reason:
                outcome = {"status": "skipped", "reason": reason}
            else:
                backoff = state["youtube_backoff"] if youtube else None
                if attempted >= capacity or (
                    backoff and _utc_date(backoff["until"]) > self.clock()
                ):
                    break
                if ingest is None:
                    raise RuntimeError("Auto-ingest requires an ingestion service.")
                attempted += 1
                if youtube:
                    self.pause(YOUTUBE_INGEST_PAUSE_SECONDS)
                outcome = ingest(subscription, row["item"], row["show"])
                throttle = outcome["reason"] in {"rate_limited", "request_blocked"}
                if youtube and throttle:
                    failures = min(backoff["failures"] + 1, 7) if backoff else 1
                    delay = max(min(60 * 2 ** (failures - 1), 3600), outcome.get("retry_after", 0))
                    state["youtube_backoff"] = {
                        "until": (self.clock() + timedelta(seconds=delay)).isoformat(),
                        "failures": failures,
                    }
                    self._save_state(state)
                elif backoff:
                    state["youtube_backoff"] = None
                    self._save_state(state)
            self._apply_outcome(row, outcome)
            self._save_releases(releases)
            changed.append(row)
            if row["reason"] in {"rate_limited", "request_blocked"}:
                break
        return attempted, changed

    def poll_all(self, *, dry_run=False):
        """Poll every subscription, reporting feed failures per subscription."""
        results = []
        for subscription in self.list():
            subscription_id = subscription["id"]
            try:
                results.append(self.poll(subscription_id, dry_run=dry_run))
            except FeedPollError as exc:
                results.append({"subscription_id": subscription_id, "error": str(exc)})
            except SubscriptionNotFound:
                continue
        return results


def start_poller(get_store, minutes):
    """Start periodic polling after the first positive interval has elapsed."""
    try:
        minutes = float(minutes)
    except (ValueError, TypeError) as exc:
        raise ValueError("YT_RAG_FEED_POLL_MINUTES must be a finite, non-negative interval.") from exc
    if not math.isfinite(minutes) or minutes < 0 or minutes * 60 > threading.TIMEOUT_MAX:
        raise ValueError("YT_RAG_FEED_POLL_MINUTES must be a finite, non-negative interval.")
    stop = threading.Event()
    if minutes:
        def run():
            while not stop.wait(minutes * 60):
                try:
                    for result in get_store().poll_all():
                        if "error" in result:
                            logging.warning("Feed poll failed for %s: %s",
                                            result["subscription_id"], result["error"])
                except Exception:
                    logging.exception("Feed polling failed")

        threading.Thread(target=run, name="feed-poller", daemon=True).start()
    return stop


def main(argv=None):
    """List subscriptions or poll them from a standalone process."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    poll_parser = commands.add_parser("poll")
    poll_parser.add_argument("--sub", metavar="ID")
    poll_parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    store = SubscriptionStore()
    try:
        if args.command == "list":
            print(json.dumps(store.list(), ensure_ascii=False, indent=2))
            return 0

        # Construct the library only when a release needs ingestion or reconciliation.
        service_store = None

        def call_service(method, *values):
            nonlocal service_store
            with redirect_stdout(sys.stderr):
                if service_store is None:
                    from local_preview import local_api
                    service = local_api.SERVICE or local_api.LocalRAGService()
                    service_store = service.subscription_store(wait_for_audio=True)
                return getattr(service_store, method)(*values)

        store.ingest_youtube = lambda *values: call_service("ingest_youtube", *values)
        store.ingest_podcast = lambda *values: call_service("ingest_podcast", *values)
        store.podcast_status = lambda *values: call_service("podcast_status", *values)
        with nullcontext() if args.dry_run else library_writer():
            result = (
                store.poll(args.sub, dry_run=args.dry_run) if args.sub
                else store.poll_all(dry_run=args.dry_run)
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(isinstance(result, list) and any("error" in row for row in result))
    except (SubscriptionNotFound, FeedPollError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
