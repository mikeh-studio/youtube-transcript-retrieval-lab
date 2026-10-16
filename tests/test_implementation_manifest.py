"""Implementation identities are independent of mutable experiment data."""

from retrieval_lab.provenance import implementation_manifest


def test_identity_is_repeatable_and_includes_serving_and_scoring_code(
    monkeypatch, tmp_path
):
    before = implementation_manifest()
    monkeypatch.setenv("YT_RAG_DATA_DIR", str(tmp_path))
    (tmp_path / "private-review.json").write_text('{"label": "changed"}')
    assert implementation_manifest() == before
    assert before["schema_version"] == "retrieval-lab-implementation/v1"
    for path in (
        "browser_boundary.py",
        "local_preview/local_api.py",
        "local_preview/config.py",
        "retrieval_lab/grounded_answer.py",
        "multilingual/video_library.py",
        "evals/scoring.py",
        "local_preview/schemas/eval_generator_output.schema.json",
        "local_preview/web/assets/index.js",
    ):
        assert len(before["files"][path]) == 64
    assert all(
        not path.startswith("/") and "private-review" not in path
        for path in before["files"]
    )
