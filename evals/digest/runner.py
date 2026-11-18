"""Run offline digest scoring or exercise the synthetic sanity cases."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from evals.digest.scoring import score_digest
from local_preview.digest import DigestService


def _jsonl(path):
    if path is None:
        return []
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def run_sanity(mode):
    """Exercise a repeated claim or an empty prior scope with an offline classifier."""
    if mode not in ("self_novelty", "empty_library"):
        raise ValueError("Unknown digest sanity mode")
    text = "The copper kite uses a wicker tail."
    copies = {
        "release": {
            "title": "Copper kite notes",
            "published_at": "2026-02-02",
            "language": "en",
        },
    }
    retrievals = []
    classifications = []
    if mode == "self_novelty":
        copies["older_copy"] = {"published_at": "2026-02-01", "language": "en"}

    def key_areas(_source_id, **_options):
        return {
            "sections": [
                {
                    "key_points": [
                        {"text": text, "start": 0, "end": 3, "evidence_lines": [1]},
                    ]
                }
            ]
        }

    def retrieve(query, **options):
        if options["video_ids"] != ["older_copy"]:
            raise RuntimeError(
                "Sanity retrieval scope did not contain just the older copy"
            )
        retrievals.append(query)
        return {
            "results": [{"video_id": "older_copy", "text": text, "start": 0, "end": 3}]
        }

    def classify(**request):
        rows = []
        for claim in json.loads(request["user_message"])["claims"]:
            matching = [
                row["passage_id"]
                for row in claim["prior_passages"]
                if row["text"] == claim["text"]
            ]
            rows.append(
                {
                    "claim_id": claim["claim_id"],
                    "relation": "covered" if matching else "new",
                    "prior": matching,
                    "reason": "Exact-match fixture comparison.",
                }
            )
        classifications.append(rows)
        return {"text": json.dumps({"claims": rows})}

    with TemporaryDirectory() as runtime:
        service = DigestService(
            runtime,
            library=SimpleNamespace(videos=copies),
            retrieve_fn=retrieve,
            key_areas_fn=key_areas,
            llm=classify,
            clock=lambda: "2026-02-03T00:00:00Z",
        )
        options = {"language": "en", "provider": "fixture", "model": "exact_match"}
        if mode == "empty_library":
            release = copies.pop("release")
            empty = service.build_digest(
                window={"start": "2026-02-01T00:00:00Z", "end": "2026-02-03T00:00:00Z"},
                **options,
            )
            if (
                empty["releases"]
                or empty["quality"]["claims_checked"]
                or classifications
            ):
                raise RuntimeError(
                    "Empty library unexpectedly generated a release or checked claims"
                )
            copies["release"] = release
        digest = service.build_digest(source_ids=["release"], **options)
    claim = digest["releases"][0]["claims"][0]
    expected = "covered" if mode == "self_novelty" else "new"
    if (
        claim["relation"] != expected
        or len(retrievals) != (mode == "self_novelty")
        or len(classifications) != 1
        or claim["downgraded"]
    ):
        raise RuntimeError(f"Digest sanity check failed: {mode}")
    return {
        "mode": mode,
        "status": "passed",
        "relation": claim["relation"],
        "quality": digest["quality"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--self-novelty",
        action="store_true",
        help="compare against an earlier identical claim",
    )
    modes.add_argument(
        "--empty-library",
        action="store_true",
        help="check an empty library and a first release",
    )
    parser.add_argument(
        "--digest", type=Path, help="saved digest JSON for citation scoring"
    )
    parser.add_argument(
        "--labels", type=Path, help="finalized digest_novelty labels JSONL"
    )
    parser.add_argument(
        "--preferences", type=Path, help="JSONL choices: digest, baseline, or tie"
    )
    args = parser.parse_args(argv)
    has_inputs = args.digest or args.labels or args.preferences
    if args.self_novelty or args.empty_library:
        if has_inputs:
            parser.error("Sanity modes do not take scoring files")
        result = run_sanity("self_novelty" if args.self_novelty else "empty_library")
    elif has_inputs:
        digest = (
            json.loads(args.digest.read_text(encoding="utf-8")) if args.digest else None
        )
        result = score_digest(digest, _jsonl(args.labels), _jsonl(args.preferences))
    else:
        parser.error("Choose a sanity mode or provide at least one scoring file")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
