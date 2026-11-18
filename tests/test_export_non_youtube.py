"""Mixed libraries preserve the YouTube export contract."""

import json
import sys

import pytest

from pipelines.export_library import build_export, canonical_json, main, sha256
from tests.test_library_export import seed


@pytest.fixture
def mixed_library(tmp_path):
    root, golden = seed(tmp_path)
    manifest_path = root / "library" / "library.json"
    manifest = json.loads(manifest_path.read_text())
    audio_sources = [
        ("pod_0123456789abcdef", "podcast_rss"),
        ("aud_fedcba9876543210", "local_audio"),
    ]
    for source_id, platform in audio_sources:
        path = root / "library" / "videos" / source_id / "record.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"source": {"platform": platform}, "title": "Clay radio"}))
        manifest["videos"].insert(1, source_id)
    manifest_path.write_text(json.dumps(manifest))
    return root, golden, manifest_path


def test_mixed_export_filters_manifest_and_keeps_youtube_records(mixed_library):
    root, golden, manifest_path = mixed_library
    before = manifest_path.read_bytes()
    export = build_export(root)
    assert export["videos"] == golden["videos"]
    assert json.loads(export["manifest_json"]) == json.loads(golden["manifest_json"])
    assert export["manifest_sha256"] == sha256(export["manifest_json"].encode())
    identity = {
        "schema_version": export["schema_version"],
        "manifest_sha256": export["manifest_sha256"],
        "videos": [
            {"video_id": row["video_id"], "record_sha256": row["record_sha256"]}
            for row in export["videos"]
        ],
    }
    assert export["snapshot_id"] == sha256(canonical_json(identity).encode())
    assert manifest_path.read_bytes() == before


def test_mixed_export_cli_reports_skips(mixed_library, tmp_path, monkeypatch, capsys):
    root, _, _ = mixed_library
    output = tmp_path / "bundle.json"
    monkeypatch.setattr(sys, "argv", [
        "export_library", "--data-dir", str(root), "--output", str(output),
    ])
    main()
    bundle = json.loads(output.read_text())
    assert json.loads(capsys.readouterr().out) == {
        "snapshot_id": bundle["snapshot_id"],
        "videos": 2,
        "skipped_non_youtube": 2,
        "output": str(output),
    }
    assert "skipped_non_youtube" not in bundle


def test_audio_only_library_cannot_emit_empty_v1_bundle(mixed_library):
    root, _, path = mixed_library
    manifest = json.loads(path.read_text())
    manifest["videos"] = [source_id for source_id in manifest["videos"] if len(source_id) != 11]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="no YouTube records"):
        build_export(root)
