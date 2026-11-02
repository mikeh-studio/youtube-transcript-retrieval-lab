from types import SimpleNamespace

import pytest

from local_preview.evidence_workspace import EvidenceWorkspace


class FakeService:
    def __init__(self):
        self.engine = SimpleNamespace(
            library=SimpleNamespace(
                videos={
                    "vid": {
                        "chunks": [
                            {"raw_text": "First", "start": 1, "end": 2},
                            {"raw_text": "Second", "start": 3, "end": 4},
                        ],
                        "language": "en",
                    }
                }
            )
        )
        self.calls = []

    def _all_chunks(self, video_id):
        return [
            {
                "video_id": video_id,
                "chunk_index": i,
                "text": c["raw_text"],
                "start": c["start"],
                "end": c["end"],
                "url": f"https://www.youtube.com/watch?v={video_id}&t={c['start']}s",
            }
            for i, c in enumerate(self.engine.library.videos[video_id]["chunks"])
        ]

    def _resolve_llm_model(self, provider, model):
        if provider not in ("openai", "claude"):
            raise ValueError("invalid provider")
        return model or "model"

    def ask_with_sources(self, query, sources, **kwargs):
        self.calls.append((query, sources, kwargs))
        return {"status": "answered", "citations": []}

    def retrieve(self, *args, **kwargs):
        raise AssertionError("Must not retrieve again")


def selection(workspace):
    return workspace.create(
        {
            "query": "Who?",
            "video_ids": ["vid"],
            "passages": [{"video_id": "vid", "chunk_index": 0, "text": "First"}],
        }
    )


def test_exact_selection_and_context_do_not_trigger_retrieval():
    service = FakeService()
    workspace = EvidenceWorkspace(service)
    snap = selection(workspace)
    passage_id = snap["passages"][0]["id"]
    context = workspace.context(
        {"snapshot_id": snap["snapshot_id"], "passage_id": passage_id}
    )
    assert [x["text"] for x in context["passages"]] == ["First", "Second"]
    body = {
        "snapshot_id": snap["snapshot_id"],
        "passage_ids": [passage_id],
        "provider": "openai",
        "max_output_tokens": 123,
    }
    estimate = workspace.estimate(body)
    body.update(estimate_id=estimate["estimate_id"], cost_acknowledged=True)
    assert estimate["estimated_cost"] is None
    workspace.answer(body)
    query, rows, kwargs = service.calls[0]
    assert query == "Who?" and [r["text"] for r in rows] == ["First"]
    assert kwargs["max_output_tokens"] == 123 and kwargs["strict_evidence"] is True
    with pytest.raises(ValueError, match="Unknown passage"):
        workspace.answer({**body, "passage_ids": [context["passages"][1]["id"]]})


def test_reject_tampered_scope_and_stale_snapshots():
    service = FakeService()
    workspace = EvidenceWorkspace(service)
    with pytest.raises(ValueError, match="does not match"):
        workspace.create(
            {
                "query": "q",
                "video_ids": ["vid"],
                "passages": [{"video_id": "vid", "chunk_index": 0, "text": "forged"}],
            }
        )
    with pytest.raises(ValueError, match="scope"):
        workspace.create(
            {
                "query": "q",
                "video_ids": ["vid"],
                "passages": [{"video_id": "other", "chunk_index": 0}],
            }
        )
    snap = selection(workspace)
    service.engine.library.videos["vid"]["chunks"][1]["raw_text"] = "reindexed"
    with pytest.raises(ValueError, match="stale"):
        workspace.get(snap["snapshot_id"])
    del service.engine.library.videos["vid"]
    with pytest.raises(ValueError, match="no longer exists"):
        workspace.get(snap["snapshot_id"])


def test_estimate_and_ack_are_required_and_bound_to_exact_request():
    service = FakeService()
    workspace = EvidenceWorkspace(service)
    snap = selection(workspace)
    body = {
        "snapshot_id": snap["snapshot_id"],
        "passage_ids": [snap["passages"][0]["id"]],
        "provider": "openai",
        "max_output_tokens": 100,
    }
    with pytest.raises(ValueError, match="acknowledge"):
        workspace.answer(body)
    estimate = workspace.estimate(body)
    with pytest.raises(ValueError, match="acknowledge"):
        workspace.answer({**body, "estimate_id": estimate["estimate_id"]})
    with pytest.raises(ValueError, match="acknowledge"):
        workspace.answer(
            {
                **body,
                "max_output_tokens": 101,
                "estimate_id": estimate["estimate_id"],
                "cost_acknowledged": True,
            }
        )
    with pytest.raises(ValueError, match="acknowledge"):
        workspace.answer(
            {
                **body,
                "answer_language": "ja",
                "estimate_id": estimate["estimate_id"],
                "cost_acknowledged": True,
            }
        )
    with pytest.raises(ValueError, match="acknowledge"):
        workspace.answer(
            {
                **body,
                "provider": "claude",
                "estimate_id": estimate["estimate_id"],
                "cost_acknowledged": True,
            }
        )
    with pytest.raises(ValueError, match="acknowledge"):
        workspace.answer(
            {
                **body,
                "model": "another-model",
                "estimate_id": estimate["estimate_id"],
                "cost_acknowledged": True,
            }
        )
    assert not service.calls
    workspace.answer(
        {**body, "estimate_id": estimate["estimate_id"], "cost_acknowledged": True}
    )
    assert len(service.calls) == 1


def test_neighbor_requires_fresh_snapshot_and_old_scope_does_not_mutate():
    service = FakeService()
    workspace = EvidenceWorkspace(service)
    snap = selection(workspace)
    neighbor = workspace.context(
        {"snapshot_id": snap["snapshot_id"], "passage_id": snap["passages"][0]["id"]}
    )["passages"][1]
    new = workspace.create(
        {"query": "Who?", "video_ids": ["vid"], "passages": [neighbor]}
    )
    assert new["snapshot_id"] != snap["snapshot_id"]
    assert workspace.get(snap["snapshot_id"])["passages"][0]["text"] == "First"
    with pytest.raises(ValueError, match="Unknown passage"):
        workspace.estimate(
            {"snapshot_id": snap["snapshot_id"], "passage_ids": [neighbor["id"]]}
        )


@pytest.mark.parametrize("invalid", [0, -1, 4097, True, 1.5, "100"])
def test_invalid_output_limits_rejected(invalid):
    workspace = EvidenceWorkspace(FakeService())
    snap = selection(workspace)
    with pytest.raises(ValueError, match="max_output_tokens"):
        workspace.estimate(
            {
                "snapshot_id": snap["snapshot_id"],
                "passage_ids": [snap["passages"][0]["id"]],
                "provider": "openai",
                "max_output_tokens": invalid,
            }
        )


def test_malformed_references_are_client_errors():
    workspace = EvidenceWorkspace(FakeService())
    with pytest.raises(ValueError):
        workspace.create(
            {
                "query": "q",
                "video_ids": ["vid"],
                "passages": [{"video_id": [], "chunk_index": 0}],
            }
        )
    with pytest.raises(ValueError):
        workspace.get([])
    snap = selection(workspace)
    with pytest.raises(ValueError):
        workspace.estimate({"snapshot_id": snap["snapshot_id"], "passage_ids": [[]]})


def test_snapshot_store_is_bounded():
    workspace = EvidenceWorkspace(FakeService())
    first = selection(workspace)
    for i in range(50):
        workspace.create(
            {
                "query": f"Question {i}",
                "video_ids": ["vid"],
                "passages": [{"video_id": "vid", "chunk_index": 0}],
            }
        )
    assert len(workspace.snapshots) == 50
    with pytest.raises(ValueError, match="not found"):
        workspace.get(first["snapshot_id"])
