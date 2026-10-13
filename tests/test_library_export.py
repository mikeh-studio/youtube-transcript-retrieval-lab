"""Transport contract tests use synthetic evidence only, without models/network."""

import json
from pathlib import Path

import pytest

from pipelines.export_library import (
    build_export,
    canonical_json,
    parse_json,
    write_export,
)

FIXTURE = Path(__file__).parent / "fixtures/library_export/v1.json"


def seed(tmp_path):
    bundle = parse_json(FIXTURE.read_text())
    root = tmp_path / "data"
    lib = root / "library"
    lib.mkdir(parents=True)
    (lib / "library.json").write_text(bundle["manifest_json"])
    for video in bundle["videos"]:
        folder = lib / "videos" / video["video_id"]
        folder.mkdir(parents=True)
        (folder / "record.json").write_text(video["record_json"])
    return root, bundle


def test_export_matches_portable_golden_and_preserves_every_source_byte(tmp_path):
    root, golden = seed(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*.json")}
    first = build_export(root)
    assert first == golden
    assert build_export(root) == first
    assert {p: p.read_bytes() for p in before} == before
    output = tmp_path / "export.json"
    write_export(first, output)
    assert output.read_text() == FIXTURE.read_text()
    assert output.stat().st_mode & 0o777 == 0o600


def test_export_refuses_overwrite_and_leaves_destination_intact(tmp_path):
    root, _ = seed(tmp_path)
    output = tmp_path / "export.json"
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        write_export(build_export(root), output)
    assert output.read_bytes() == b"existing"


@pytest.mark.parametrize("bad_id", ["../record", "short", 123])
def test_export_rejects_unsafe_or_invalid_identity(tmp_path, bad_id):
    root, _ = seed(tmp_path)
    path = root / "library/library.json"
    manifest = json.loads(path.read_text())
    manifest["videos"] = [bad_id]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="invalid YouTube"):
        build_export(root)


def test_export_rejects_duplicate_manifest_identity(tmp_path):
    root, _ = seed(tmp_path)
    path = root / "library/library.json"
    manifest = json.loads(path.read_text())
    manifest["videos"].append(manifest["videos"][0])
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="duplicate video"):
        build_export(root)


def test_export_fails_for_missing_record_without_creating_output(tmp_path):
    root, _ = seed(tmp_path)
    (root / "library/videos/fixtureEN01/record.json").unlink()
    with pytest.raises(FileNotFoundError):
        build_export(root)


def test_export_rejects_record_symlink(tmp_path):
    root, _ = seed(tmp_path)
    record = root / "library/videos/fixtureEN01/record.json"
    external = tmp_path / "external.json"
    external.write_bytes(record.read_bytes())
    record.unlink()
    record.symlink_to(external)
    with pytest.raises(ValueError, match="escapes"):
        build_export(root)


@pytest.mark.parametrize(
    "payload", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}']
)
def test_strict_json_rejects_ambiguous_values(payload):
    with pytest.raises(ValueError):
        parse_json(payload)


def test_export_detects_concurrent_source_change(tmp_path, monkeypatch):
    root, _ = seed(tmp_path)
    path = root / "library/videos/fixtureJA01/record.json"
    original = Path.read_bytes
    reads = 0

    def read(p):
        nonlocal reads
        if p == path:
            reads += 1
            if reads > 1:
                return b"changed"
        return original(p)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(ValueError, match="changed during export"):
        build_export(root)


def test_record_byte_change_changes_snapshot_identity(tmp_path):
    root, _ = seed(tmp_path)
    first = build_export(root)
    p = root / "library/videos/fixtureJA01/record.json"
    p.write_text(p.read_text() + "\n")
    second = build_export(root)
    assert first["snapshot_id"] != second["snapshot_id"]
    assert canonical_json(first) != canonical_json(second)


def test_manifest_version_rejects_float_coercion(tmp_path):
    root, _ = seed(tmp_path)
    p = root / "library/library.json"
    manifest = json.loads(p.read_text())
    manifest["format_version"] = 2.0
    p.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="format_version"):
        build_export(root)
