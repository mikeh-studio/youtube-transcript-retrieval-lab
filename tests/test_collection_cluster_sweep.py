"""Exercise the cache-only clustering sweep using synthetic sections."""

import json
import stat

import pytest

from evals.summary import cluster_sweep
from local_preview.key_areas import KEY_AREAS_VERSION


def _cache_entry(title):
    return {"sections": [{"title": title, "summary": "Sample tag.", "start": 0, "end": 10}]}


def _cache_file(data_dir, source_id, entries):
    path = data_dir / "cache" / "key_areas" / f"{source_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({json.dumps(key): entry for key, entry in entries}))
    return path


def _cache_key(source_id, *, language="en", model="fixture", version=KEY_AREAS_VERSION):
    return [version, language, "fixture-provider", model, f"fingerprint-{source_id}"]


def test_sweep_cli_uses_cached_sections_once_per_set_and_writes_only_counts(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(cluster_sweep, "ROOT", tmp_path)
    data_dir = tmp_path / "my-data"
    out = tmp_path / "evals" / "reports" / "clusters.json"
    cork = _cache_entry("Cork")
    cork["sections"].extend(_cache_entry("Cork")["sections"])
    paths = [
        _cache_file(data_dir, "jar-a", [
            (_cache_key("jar-a"), cork),
            (_cache_key("jar-a", language="ja"), _cache_entry("Thread")),
            (_cache_key("jar-a", model="other"), _cache_entry("Foil")),
            (_cache_key("jar-a", version=KEY_AREAS_VERSION - 1), _cache_entry("Wire")),
        ]),
        _cache_file(data_dir, "jar-b", [(_cache_key("jar-b"), _cache_entry("Clay"))]),
        _cache_file(data_dir, "jar-c", [
            (_cache_key("jar-c") + ["https://example.test/demo"], _cache_entry("Felt")),
        ]),
    ]
    original_bytes = [path.read_bytes() for path in paths]
    calls = []

    def embed(texts):
        calls.append(texts)
        vectors = {"Cork": [1, 0], "Clay": [0.87, 0.49], "Felt": [0, 1]}
        return [vectors[text.split()[0]] for text in texts]

    cluster_sweep.main([
        "--data-dir", str(data_dir), "--source-ids", "jar-b, jar-a",
        "--source-ids", "jar-a,jar-c", "--language", "en",
        "--thresholds", "0.80,0.85,0.88,0.90", "--provider", "fixture-provider",
        "--model", "fixture", "--out", str(out),
    ], embed_fn=embed)

    assert calls == [
        ["Cork Sample tag.", "Cork Sample tag.", "Clay Sample tag."],
        ["Cork Sample tag.", "Cork Sample tag.", "Felt Sample tag."],
    ]
    assert [path.read_bytes() for path in paths] == original_bytes
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    output = out.read_text()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert all(text not in output for text in ("Cork", "Clay", "Felt", "Sample tag."))
    report = json.loads(output)
    assert report["schema"] == "collection-cluster-sweep/v1"
    first, second = report["sets"]
    assert first["source_ids"] == ["jar-b", "jar-a"]
    assert first["sections"] == 3
    assert first["cache_entries"][0]["source_fingerprint"] == "fingerprint-jar-b"
    assert first["results"] == [
        {
            "threshold": threshold, "clusters": len(groups), "cluster_sizes": sizes,
            "distinct_sources": [len(group) for group in groups], "cluster_source_ids": groups,
        }
        for threshold, groups, sizes in (
            (0.80, [["jar-a", "jar-b"]], [3]),
            (0.85, [["jar-a", "jar-b"]], [3]),
            (0.88, [["jar-a"], ["jar-b"]], [2, 1]),
            (0.90, [["jar-a"], ["jar-b"]], [2, 1]),
        )
    ]
    assert [result["clusters"] for result in second["results"]] == [2, 2, 2, 2]


@pytest.mark.parametrize(("entries", "message"), [
    ([], "Cannot read JSON input jar-a.json"),
    ([(_cache_key("jar-a", language="ja"), _cache_entry("Felt"))],
     "No matching current key-area cache entry"),
    ([(_cache_key("jar-a"), _cache_entry("Felt")),
      (_cache_key("jar-a", model="other"), _cache_entry("Foil"))],
     "Multiple key-area cache entries"),
])
def test_bad_cache_fails_before_embedding_or_writing(tmp_path, monkeypatch, capsys, entries, message):
    monkeypatch.setattr(cluster_sweep, "ROOT", tmp_path)
    if entries:
        _cache_file(tmp_path / "data", "jar-a", entries)
    out = tmp_path / "evals" / "reports" / "clusters.json"

    def unexpected(_):
        pytest.fail("Embedding should not run for unavailable or ambiguous caches")

    with pytest.raises(SystemExit) as exc:
        cluster_sweep.main([
            "--data-dir", str(tmp_path / "data"), "--source-ids", "jar-a,jar-b",
            "--out", str(out),
        ], embed_fn=unexpected)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    assert "Felt" not in captured.err and "Foil" not in captured.err
    assert not out.exists()
