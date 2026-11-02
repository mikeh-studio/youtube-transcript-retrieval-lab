"""Canonical, immutable evidence selections for the local workspace."""

import hashlib
import json
import math
from copy import deepcopy


def _digest(value):
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class EvidenceWorkspace:
    def __init__(self, service):
        self.service = service
        self.snapshots = {}
        self.estimates = {}

    def _fingerprint(self, video_id):
        video = self.service.engine.library.videos.get(video_id)
        if video is None:
            raise ValueError("Selected source no longer exists.")
        return _digest(
            {
                "chunks": video.get("chunks", []),
                "full_transcript": video.get("full_transcript"),
                "language": video.get("language"),
                "url": video.get("url"),
            }
        )

    def _row(self, video_id, index):
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError("chunk_index must be a non-negative integer.")
        matches = self.service._all_chunks(video_id=video_id)
        if index >= len(matches):
            raise ValueError("Selected passage no longer exists.")
        row = matches[index]
        row["id"] = _digest(
            {
                "video_id": video_id,
                "chunk_index": index,
                "fingerprint": self._fingerprint(video_id),
            }
        )
        return row

    def create(self, body):
        query = body.get("query")
        ids = body.get("video_ids")
        refs = body.get("passages")
        if not isinstance(query, str) or not query.strip() or len(query) > 10000:
            raise ValueError("A non-empty query is required.")
        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(x, str) or not x for x in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("video_ids must be a non-empty list of unique source IDs.")
        if not isinstance(refs, list) or not refs or len(refs) > 100:
            raise ValueError("passages must contain 1 to 100 canonical references.")
        fingerprints = {vid: self._fingerprint(vid) for vid in ids}
        rows, seen = [], set()
        for ref in refs:
            if (
                not isinstance(ref, dict)
                or not isinstance(ref.get("video_id"), str)
                or ref.get("video_id") not in fingerprints
            ):
                raise ValueError("Passage is outside the selected source scope.")
            row = self._row(ref["video_id"], ref.get("chunk_index"))
            if "text" in ref and ref["text"] != row["text"]:
                raise ValueError("Supplied passage text does not match the source.")
            if row["id"] in seen:
                raise ValueError("Duplicate passage.")
            seen.add(row["id"])
            rows.append(row)
        config = body.get("config") or {}
        if not isinstance(config, dict):
            raise ValueError("config must be an object.")
        config = {
            k: config[k]
            for k in (
                "retrieval_mode",
                "retrieval_profile",
                "reranker",
                "k",
                "language",
            )
            if k in config
            and isinstance(config[k], (str, int, float, bool, type(None)))
        }
        snapshot = {
            "query": query.strip(),
            "video_ids": ids[:],
            "passages": rows,
            "source_fingerprints": fingerprints,
            "config": config,
        }
        snapshot_id = _digest(snapshot)
        snapshot["snapshot_id"] = snapshot_id
        if sum(len(row["text"]) for row in rows) > 2_000_000:
            raise ValueError("Selected evidence is too large.")
        if snapshot_id not in self.snapshots and len(self.snapshots) >= 50:
            self.snapshots.pop(next(iter(self.snapshots)))
        self.snapshots[snapshot_id] = deepcopy(snapshot)
        return deepcopy(snapshot)

    def get(self, snapshot_id):
        snapshot = (
            self.snapshots.get(snapshot_id) if isinstance(snapshot_id, str) else None
        )
        if snapshot is None:
            raise ValueError("Evidence snapshot not found; create a new snapshot.")
        for vid, fingerprint in snapshot["source_fingerprints"].items():
            if self._fingerprint(vid) != fingerprint:
                raise ValueError(
                    "Evidence snapshot is stale; search and select passages again."
                )
        return deepcopy(snapshot)

    def selected(self, body):
        snapshot = self.get(body.get("snapshot_id"))
        ids = body.get("passage_ids")
        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids))
        ):
            raise ValueError("Select at least one unique passage ID.")
        index = {r["id"]: r for r in snapshot["passages"]}
        if any(not isinstance(i, str) or i not in index for i in ids):
            raise ValueError("Unknown passage ID for this snapshot.")
        return snapshot, [index[i] for i in ids]

    def context(self, body):
        snapshot = self.get(body.get("snapshot_id"))
        row = next(
            (r for r in snapshot["passages"] if r["id"] == body.get("passage_id")), None
        )
        if row is None:
            raise ValueError("Unknown passage ID for this snapshot.")
        chunks = self.service.engine.library.videos[row["video_id"]].get("chunks", [])
        return {
            "snapshot_id": snapshot["snapshot_id"],
            "passages": [
                self._row(row["video_id"], i)
                for i in range(
                    max(0, row["chunk_index"] - 1),
                    min(len(chunks), row["chunk_index"] + 2),
                )
            ],
        }

    def budget(self, body):
        snapshot, rows = self.selected(body)
        provider = str(body.get("provider") or "chatgpt").lower()
        model = self.service._resolve_llm_model(provider, body.get("model"))
        language = body.get("answer_language")
        if language == "match":
            language = None
        if language not in (None, "en", "ja"):
            raise ValueError("answer_language must be en or ja, or omitted.")
        limit = body.get("max_output_tokens", 800)
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 4096
        ):
            raise ValueError("max_output_tokens must be an integer between 1 and 4096.")
        # Character heuristic, deliberately conservative for multilingual text; not a tokenizer.
        input_tokens = (
            math.ceil((len(snapshot["query"]) + sum(len(r["text"]) for r in rows)) / 2)
            + 500
        )
        estimate = {
            "snapshot_id": snapshot["snapshot_id"],
            "passage_ids": body["passage_ids"][:],
            "provider": provider,
            "model": model,
            "answer_language": language,
            "input_tokens_estimate": input_tokens,
            "max_output_tokens": limit,
            "total_tokens_estimate": input_tokens + limit,
            "estimated_cost": None,
            "price_basis": None,
            "estimate_basis": "Approximate character heuristic plus prompt allowance; not a tokenizer.",
        }
        estimate["estimate_id"] = _digest(estimate)
        return snapshot, rows, estimate

    def estimate(self, body):
        _, _, estimate = self.budget(body)
        if estimate["estimate_id"] not in self.estimates and len(self.estimates) >= 200:
            self.estimates.pop(next(iter(self.estimates)))
        self.estimates[estimate["estimate_id"]] = deepcopy(estimate)
        return estimate

    def answer(self, body):
        snapshot, rows, estimate = self.budget(body)
        estimate_id = body.get("estimate_id")
        if (
            body.get("cost_acknowledged") is not True
            or not isinstance(estimate_id, str)
            or self.estimates.get(estimate_id) != estimate
        ):
            raise ValueError(
                "Review the current estimate and acknowledge that cost is unavailable before generating an answer."
            )
        result = self.service.ask_with_sources(
            snapshot["query"],
            rows,
            provider=estimate["provider"],
            model=estimate["model"],
            max_output_tokens=estimate["max_output_tokens"],
            strict_evidence=True,
            output_language=estimate["answer_language"],
        )
        return {
            "snapshot_id": snapshot["snapshot_id"],
            "passage_ids": body["passage_ids"],
            **result,
        }


def settings(service, model_options):
    providers = []
    for provider, key in [
        ("chatgpt", "OPENAI_API_KEY"),
        ("claude", "ANTHROPIC_API_KEY"),
        ("sakana", "SAKANA_API_KEY"),
    ]:
        providers.append(
            {
                "id": provider,
                "configured": service._study_provider_available(provider),
                "default_model": service._resolve_llm_model(provider, None),
                "models": model_options.get(provider, []),
            }
        )
    return {
        "providers": providers,
        "defaults": {
            "provider": "chatgpt",
            "model": service._resolve_llm_model("chatgpt", None),
            "max_output_tokens": 800,
        },
    }
