"""Build and persist release digests and item feedback."""

import json
import re
import threading
from collections import Counter
from pathlib import Path
from uuid import uuid4

from local_preview.local_files import json_bytes, write_atomic
from local_preview.novelty import (
    check_claims,
    extract_claims,
    prior_scope,
    release_date,
    source_date,
    translate_claims,
)


DIGEST_ID = re.compile(r"digest_[0-9a-f]{32}")
NOVELTY_WEIGHTS = {"new": 1.0, "contradicts": 1.0, "extends": 0.6}
REVIEW_RELATIONS = ("new", "contradicts")
DEFAULT_MAX_CLAIMS = 12


class DigestNotFound(LookupError):
    """The specified digest does not exist."""


class DigestService:
    """Build digests using injected library, retrieval, generation, and clock."""

    def __init__(
        self,
        runtime_dir,
        *,
        library,
        retrieve_fn,
        key_areas_fn,
        llm,
        clock,
        ingest_log_paths=None,
    ):
        self.directory = Path(runtime_dir) / "digests"
        self.feedback_path = Path(runtime_dir) / "digest_feedback.jsonl"
        self.library = library
        self.retrieve_fn = retrieve_fn
        self.key_areas_fn = key_areas_fn
        self.llm = llm
        self.clock = clock
        self.ingest_log_paths = ingest_log_paths
        self._feedback_lock = threading.Lock()

    def build_digest(self, *, window=None, source_ids=None, language, provider, model):
        """Build a release snapshot over exactly one window or source selection."""
        records = self.library.videos.copy()
        if self.ingest_log_paths is not None:
            undated = {
                sid for sid, record in records.items() if release_date(record) is None
            }
            if undated:
                for sid, date in _logged_ingest_dates(
                    self.ingest_log_paths(), undated
                ).items():
                    records[sid] = {**records[sid], "ingested_at": date.isoformat()}
        selected, window = _selection(records, window, source_ids)
        releases, quality = [], Counter()
        usage = Counter()

        def generate_key_areas(source_id):
            return self.key_areas_fn(
                source_id, language=language, provider=provider, model=model
            )

        def llm(**request):
            return self.llm(provider=provider, model=model, **request)

        for source_id in selected:
            record = records[source_id]
            key_areas = generate_key_areas(source_id)
            claims = extract_claims(source_id, key_areas, DEFAULT_MAX_CLAIMS)
            scope = prior_scope(records, source_id)
            checked = check_claims(
                claims,
                scope,
                self.retrieve_fn,
                lambda queries, origin, targets: translate_claims(
                    queries, origin, targets, llm
                ),
                llm,
                language=language,
                scope_languages={sid: records[sid].get("language") for sid in scope},
            )
            quality.update(
                {
                    "claims_checked": len(claims),
                    "citation_count": checked["citation_count"],
                    "valid_citation_count": checked["valid_citation_count"],
                    "downgraded_count": sum(
                        row["downgraded"] for row in checked["claims"]
                    ),
                }
            )
            usage.update(checked["usage"])
            highlights = sorted(
                (
                    {
                        **row,
                        "score": row["importance"] * NOVELTY_WEIGHTS[row["relation"]],
                    }
                    for row in checked["claims"]
                    if row["relation"] in NOVELTY_WEIGHTS
                ),
                key=lambda row: -row["score"],
            )
            source = record.get("source") or {}
            releases.append(
                {
                    "source_id": source_id,
                    "title": record.get("title") or source_id,
                    "show": (source.get("show") or source.get("channel") or {}).get(
                        "name"
                    ),
                    "published_at": release_date(record).isoformat(),
                    "source_fingerprint": key_areas.get("source_fingerprint"),
                    "prior_scope_ids": scope,
                    "claims": checked["claims"],
                    "highlights": highlights,
                    "disagreements": [
                        row for row in highlights if row["relation"] == "contradicts"
                    ],
                    "covered_count": sum(
                        row["relation"] == "covered" for row in checked["claims"]
                    ),
                }
            )

        report = {
            "digest_id": f"digest_{uuid4().hex}",
            "created_at": self.clock(),
            "window": window,
            "language": language,
            "provider": provider,
            "model": model,
            "releases": releases,
            "quality": {
                "claims_checked": quality["claims_checked"],
                "citation_validity": (
                    quality["valid_citation_count"] / quality["citation_count"]
                    if quality["citation_count"]
                    else None
                ),
                "downgraded_count": quality["downgraded_count"],
            },
            "usage": dict(usage),
        }
        write_atomic(self.directory / f"{report['digest_id']}.json", json_bytes(report))
        return report

    def get_digest(self, digest_id):
        """Load a persisted digest by its generated ID."""
        if not isinstance(digest_id, str) or not DIGEST_ID.fullmatch(digest_id):
            raise DigestNotFound("Digest not found")
        try:
            return json.loads((self.directory / f"{digest_id}.json").read_bytes())
        except FileNotFoundError as exc:
            raise DigestNotFound("Digest not found") from exc

    def list_digests(self):
        """List digest metadata for browsing and review selection."""
        summaries = []
        for path in self.directory.glob("digest_*.json"):
            if not DIGEST_ID.fullmatch(path.stem):
                continue
            digest = self.get_digest(path.stem)
            summaries.append(
                {
                    **{
                        key: digest[key]
                        for key in (
                            "digest_id",
                            "created_at",
                            "window",
                            "language",
                            "quality",
                        )
                    },
                    "release_titles": [
                        release["title"] for release in digest["releases"]
                    ],
                    "reviewable_count": sum(
                        row["relation"] in REVIEW_RELATIONS
                        for release in digest["releases"]
                        for row in release["claims"]
                    ),
                }
            )
        return sorted(
            summaries,
            key=lambda row: (row["created_at"], row["digest_id"]),
            reverse=True,
        )

    def save_feedback(self, digest_id, payload):
        """Record feedback against a visible digest item with an atomic JSONL update."""
        digest = self.get_digest(digest_id)
        item_id, rating, note = (
            payload.get("item_id"),
            payload.get("rating"),
            payload.get("note"),
        )
        if not isinstance(item_id, str) or not any(
            row["claim_id"] == item_id
            for release in digest["releases"]
            for row in release["highlights"]
        ):
            raise ValueError("item_id must identify a digest highlight")
        if rating not in ("up", "down"):
            raise ValueError("rating must be up or down")
        if note is not None and (not isinstance(note, str) or len(note) > 4000):
            raise ValueError("note must be a string of at most 4000 characters")
        row = {
            "digest_id": digest_id,
            "item_id": item_id,
            "rating": rating,
            "created_at": self.clock(),
            **({"note": note} if note is not None else {}),
        }
        with self._feedback_lock:
            try:
                existing = self.feedback_path.read_bytes()
            except FileNotFoundError:
                existing = b""
            write_atomic(self.feedback_path, existing + json_bytes(row))
        return row


def _window_bound(value):
    parsed = source_date(value, require_timezone=True)
    if parsed is None:
        raise ValueError("window bounds must be ISO 8601 timestamps with timezones")
    return parsed


def _logged_ingest_dates(paths, source_ids):
    """Recover the latest recorded successful ingest for older library records."""
    dates = {}
    for path in paths:
        try:
            stream = path.open(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        with stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("event") != "job.completed":
                    continue
                sid = row.get("video_id")
                if not isinstance(sid, str) or sid not in source_ids:
                    continue
                date = source_date(row.get("ts"))
                if date is not None and (sid not in dates or date > dates[sid]):
                    dates[sid] = date
    return dates


def _selection(records, window, source_ids):
    if (window is None) == (source_ids is None):
        raise ValueError("Provide exactly one of window or source_ids")
    if source_ids is not None:
        if (
            not isinstance(source_ids, list)
            or not source_ids
            or not all(isinstance(value, str) and value.strip() for value in source_ids)
        ):
            raise ValueError(
                "source_ids must be a nonempty list of distinct source IDs"
            )
        selected = [value.strip() for value in source_ids]
        if len(set(selected)) != len(selected):
            raise ValueError(
                "source_ids must be a nonempty list of distinct source IDs"
            )
        for source_id in selected:
            if source_id not in records:
                raise KeyError(f"source_id not found: {source_id}")
            if release_date(records[source_id]) is None:
                raise ValueError(
                    f"Source has no publication or ingest date: {source_id}"
                )
        return selected, None

    if not isinstance(window, dict) or window.keys() != {"start", "end"}:
        raise ValueError("window must contain start and end")
    start, end = _window_bound(window["start"]), _window_bound(window["end"])
    if start >= end:
        raise ValueError("window start must precede end")
    dates = {sid: release_date(record) for sid, record in records.items()}
    selected = [
        sid for sid, date in dates.items() if date is not None and start <= date < end
    ]
    selected.sort(key=lambda sid: (dates[sid], sid), reverse=True)
    return selected, {"start": start.isoformat(), "end": end.isoformat()}
