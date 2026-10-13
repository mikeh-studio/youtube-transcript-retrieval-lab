"""Export stored library records without loading models or contacting YouTube."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

SCHEMA_VERSION = "youtube-library-export/v1"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def parse_json(text):
    """Reject duplicate keys and non-standard numeric constants."""
    return json.loads(
        text,
        object_pairs_hook=_object,
        parse_constant=_invalid_constant,
        parse_float=_finite_float,
    )


def canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def build_export(data_dir: Path) -> dict:
    """Capture exact UTF-8 record bytes from the split v2 library layout.

    This is a transport exporter. Semantic evidence validation belongs to the
    receiving service. Source files are checked again before returning to catch
    concurrent changes; operators should pause library writes during export.
    """
    library_dir = (Path(data_dir) / "library").resolve()
    manifest_path = library_dir / "library.json"
    if manifest_path.is_symlink():
        raise ValueError("manifest must not be a symlink")
    manifest_bytes = manifest_path.read_bytes()
    manifest_text = manifest_bytes.decode("utf-8")
    manifest = parse_json(manifest_text)
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("format_version")) is not int
        or manifest["format_version"] != 2
    ):
        raise ValueError("only split library format_version 2 is supported")
    ids = manifest.get("videos")
    if not isinstance(ids, list) or not ids:
        raise ValueError("manifest videos must be a nonempty list")
    if any(not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid) for vid in ids):
        raise ValueError("manifest contains an invalid YouTube video ID")
    if len(set(ids)) != len(ids):
        raise ValueError("manifest contains duplicate video IDs")

    videos = []
    captured = [(manifest_path, manifest_bytes)]
    for video_id in ids:
        path = library_dir / "videos" / video_id / "record.json"
        if path.is_symlink() or not path.resolve().is_relative_to(library_dir):
            raise ValueError(f"record escapes library root: {video_id}")
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if not isinstance(parse_json(text), dict):
            raise ValueError(f"record is not an object: {video_id}")
        videos.append(
            {"video_id": video_id, "record_sha256": sha256(raw), "record_json": text}
        )
        captured.append((path, raw))

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "manifest_json": manifest_text,
        "manifest_sha256": sha256(manifest_bytes),
        "videos": videos,
    }
    identity = {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": envelope["manifest_sha256"],
        "videos": [
            {"video_id": v["video_id"], "record_sha256": v["record_sha256"]}
            for v in videos
        ],
    }
    envelope["snapshot_id"] = sha256(canonical_json(identity).encode("utf-8"))
    if any(path.read_bytes() != raw for path, raw in captured):
        raise ValueError("library changed during export; pause writes and retry")
    return envelope


def write_export(bundle: dict, output: Path) -> None:
    """Publish a private, complete bundle, refusing to overwrite any file."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    payload = (canonical_json(bundle) + "\n").encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = build_export(args.data_dir)
    write_export(bundle, args.output)
    print(
        json.dumps(
            {
                "snapshot_id": bundle["snapshot_id"],
                "videos": len(bundle["videos"]),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
