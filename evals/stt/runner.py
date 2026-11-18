"""Score finalized audio labels, paired word timestamps, and ingest timings offline."""

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median

from evals.contracts import ContractError, read_json, require_private
from local_preview.local_files import json_bytes, write_atomic

from .drift import align_drift
from .metrics import levenshtein, normalize_text


LANDING_TOLERANCE_SECONDS = 1.0


def _objects(rows, name):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ContractError(f"{name} must be a list of objects.")
    return rows


def _valid_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _validate_label(row):
    start, end = row.get("start"), row.get("end")
    if (
        not _valid_number(start) or not _valid_number(end) or not 0 <= start < end
        or row.get("review_decision") not in ("approved", "edited")
        or row.get("language") not in ("en", "ja")
        or not isinstance(row.get("source_id"), str)
        or not row["source_id"]
    ):
        raise ContractError("Labels need a source, language, excerpt bounds and an accepted review.")


def score_references(rows):
    """Micro-average errors by engine, model, and language over accepted excerpts."""
    systems = defaultdict(list)
    for row in _objects(rows, "Reference rows"):
        _validate_label(row)
        stt = row.get("stt")
        if (
            not isinstance(stt, dict)
            or any(not isinstance(stt.get(key), str) or not stt[key] for key in ("engine", "model"))
            or any(not isinstance(row.get(key), str) for key in ("text", "draft_text"))
        ):
            raise ContractError("Reference rows need reviewed text, draft text, and STT provenance.")
        language = row["language"]
        ref = normalize_text(row["text"], language)
        hyp = normalize_text(row["draft_text"], language)
        ref_words, hyp_words = ref.split(), hyp.split()
        systems[stt["engine"], stt["model"], language].append({
            "character_edits": levenshtein(ref, hyp),
            "characters": len(ref),
            "word_edits": levenshtein(ref_words, hyp_words) if language != "ja" else None,
            "words": len(ref_words),
        })
    report = []
    for (engine, model, language), excerpts in sorted(systems.items()):
        character_edits = sum(row["character_edits"] for row in excerpts)
        characters = sum(row["characters"] for row in excerpts)
        word_edits = sum(row["word_edits"] for row in excerpts) if language != "ja" else None
        words = sum(row["words"] for row in excerpts) if word_edits is not None else None
        report.append({
            "engine": engine, "model": model, "language": language,
            "excerpt_count": len(excerpts),
            "character_edits": character_edits, "reference_characters": characters,
            "word_edits": word_edits, "reference_words": words,
            "cer": character_edits / max(1, characters),
            "wer": word_edits / max(1, words) if word_edits is not None else None,
        })
    return report


def score_landing(rows):
    """Count approved landings and measured offsets within one second."""
    offsets = []
    approved = 0
    for row in _objects(rows, "Landing rows"):
        _validate_label(row)
        if row["review_decision"] == "approved":
            approved += 1
        else:
            offset = row.get("offset_seconds")
            if not _valid_number(offset) or row["start"] + offset < 0:
                raise ContractError("Edited landing rows need a valid signed offset_seconds.")
            offsets.append(offset)
    landed = approved + sum(abs(offset) <= LANDING_TOLERANCE_SECONDS for offset in offsets)
    return {
        "tolerance_seconds": LANDING_TOLERANCE_SECONDS,
        "reviewed_count": len(rows),
        "landed_count": landed,
        "landing_rate": landed / len(rows) if rows else None,
        "measured_offsets_seconds": offsets,
    }


def score_drift(pairs):
    """Align each explicitly paired caption and STT timeline."""
    seen = set()
    result = []
    for pair in _objects(pairs, "Drift pairs"):
        pair_id, language = pair.get("pair_id"), pair.get("language")
        if not isinstance(pair_id, str) or not pair_id or pair_id in seen or language not in ("en", "ja"):
            raise ContractError("Drift pairs need a distinct pair_id and language en or ja.")
        seen.add(pair_id)
        result.append({
            "pair_id": pair_id, "language": language,
            **align_drift(pair.get("caption_words"), pair.get("stt_words"), language),
        })
    return result


def score_rtf(jobs):
    """Compute transcription wall time divided by audio duration."""
    timings = []
    unavailable = 0
    seen = set()
    for job in _objects(jobs, "Ingest jobs"):
        if job.get("status") != "completed" or job.get("source_type") not in ("podcast_rss", "local_audio"):
            continue
        job_id = job.get("job_id")
        if not isinstance(job_id, str) or not job_id or job_id in seen:
            raise ContractError("Completed audio jobs need distinct job IDs.")
        seen.add(job_id)
        elapsed = job.get("transcription_seconds")
        duration = job.get("audio_duration_seconds")
        if not _valid_number(elapsed) or elapsed < 0 or not _valid_number(duration) or duration <= 0:
            unavailable += 1
            continue
        rtf = elapsed / duration
        if not math.isfinite(rtf):
            raise ContractError("Job timing produces a non-finite real-time factor.")
        timings.append({
            "job_id": job_id, "source_id": job.get("video_id"),
            "engine": job.get("stt_engine"), "model": job.get("stt_model"),
            "transcription_seconds": elapsed, "audio_duration_seconds": duration,
            "rtf": rtf,
        })
    return {
        "jobs": timings,
        "unavailable_count": unavailable,
        "median_rtf": median(row["rtf"] for row in timings) if timings else None,
    }


def load_labels(manifest_paths, task):
    """Read accepted rows only from finalized, digest-verified label datasets."""
    rows = []
    seen = set()
    for path in manifest_paths:
        require_private(path)
        manifest = read_json(path)
        if (
            not isinstance(manifest, dict) or manifest.get("version") != 1
            or manifest.get("task") != task or not path.name.endswith(".manifest.json")
        ):
            raise ContractError(f"Provide a finalized {task} manifest.")
        data = path.with_name(path.name.removesuffix(".manifest.json") + ".jsonl")
        require_private(data)
        content = data.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != manifest.get("sha256") or digest in seen:
            raise ContractError(f"Label digest mismatch or duplicate dataset for {task}.")
        seen.add(digest)
        dataset = _objects([json.loads(line) for line in content.splitlines()], "Label rows")
        fingerprints = manifest.get("source_fingerprints")
        if (
            not isinstance(fingerprints, dict) or not dataset or len(dataset) != manifest.get("row_count")
            or any(
                not isinstance(row.get("source_id"), str) or row["source_id"] not in fingerprints
                or row.get("source_fingerprint") != fingerprints[row["source_id"]]
                or row.get("task") != task or row.get("language") != manifest.get("language")
                for row in dataset
            )
        ):
            raise ContractError(f"Label metadata does not match the manifest for {task}.")
        rows.extend(dataset)
    return rows


def _read_rows(path, field):
    if path is None:
        return []
    require_private(path)
    payload = read_json(path)
    rows = payload.get(field) if isinstance(payload, dict) else payload
    return _objects(rows, field)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, action="append", default=[], metavar="MANIFEST",
                        help="finalized stt_reference manifest (repeatable)")
    parser.add_argument("--landing", type=Path, action="append", default=[], metavar="MANIFEST",
                        help="finalized ts_landing manifest (repeatable)")
    parser.add_argument("--drift-pairs", type=Path, help="JSON file with paired caption and STT timed words")
    parser.add_argument("--jobs", type=Path, help="JSON snapshot of GET /v1/ingest/jobs")
    parser.add_argument("--out", type=Path, help="JSON report file (defaults to stdout)")
    args = parser.parse_args(argv)
    if not (args.references or args.landing or args.drift_pairs or args.jobs):
        parser.error("Provide at least one evaluation input.")
    try:
        report = {
            "schema": "stt-evaluation/v1",
            "references": score_references(load_labels(args.references, "stt_reference")),
            "landing": score_landing(load_labels(args.landing, "ts_landing")),
            "drift": score_drift(_read_rows(args.drift_pairs, "pairs")),
            "rtf": score_rtf(_read_rows(args.jobs, "jobs")),
        }
        result = json_bytes(report)
        if args.out:
            require_private(args.out)
            write_atomic(args.out, result)
        else:
            print(result.decode("utf-8"), end="")
    except (OSError, ValueError, OverflowError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
