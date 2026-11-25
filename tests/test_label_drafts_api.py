"""Exercise the real label routes with a fake drafter and synthetic transcript."""

import json
import socket
from types import SimpleNamespace

import pytest

from evals.summary.dataset import load_gold
from evals.summary.judge import load_judgments
from evals.summary.predictions import load_predictions
from evals.summary.runner import audit_sample
from tests.test_eval_generator_api import _request, local_api
from tests.test_label_drafts import SOURCE_ID, TASK, wait_for_job
from tests.test_label_drafts import workflow as workflow
from tests.test_summary_eval import GOLD, JUDGMENTS, MANIFEST, PREDICTIONS


def request(method, path, body=None):
    status, _, payload = _request(method, path, body)
    return status, json.loads(payload)


def test_label_http_lifecycle(monkeypatch, workflow):
    service, _, _ = workflow
    monkeypatch.setattr(
        local_api, "SERVICE", SimpleNamespace(_label_draft_service=lambda: service)
    )
    status, payload = request("GET", "/v1/labels/tasks")
    assert status == 200 and payload["tasks"][0]["task"] == TASK
    assert request("GET", "/v1/labels/drafts")[1]["drafts"] == []

    status, payload = request(
        "POST",
        "/v1/labels/jobs",
        {"task": TASK, "source_ids": [SOURCE_ID], "language": "en"},
    )
    assert status == 202
    job_id = payload["job"]["job_id"]
    assert wait_for_job(service, job_id)["status"] == "completed"
    status, payload = request("GET", f"/v1/labels/jobs/{job_id}")
    assert status == 200 and payload["job"]["status"] == "completed"
    draft_id = payload["job"]["draft_id"]
    path = f"/v1/labels/drafts/{draft_id}"
    assert request("GET", "/v1/labels/drafts")[1]["drafts"][0]["draft_id"] == draft_id

    status, payload = request("GET", path)
    assert status == 200
    items = payload["draft"]["items"]
    status, payload = request("GET", f"{path}/context?item={items[0]['id']}")
    assert (
        status == 200
        and payload["context"]["lines"][0]["text"] == "The lane has a bend."
    )
    assert request("POST", f"{path}/finalize", {})[0] == 409

    status, payload = request(
        "POST",
        f"{path}/review",
        {
            "decisions": [{"id": item["id"], "decision": "approved"} for item in items],
        },
    )
    assert status == 200
    assert payload["draft"]["items"][0]["review"]["decision"] == "approved"
    status, payload = request("POST", f"{path}/finalize", {})
    assert status == 200 and payload["dataset"]["row_count"] == 3
    assert request("GET", "/v1/labels/datasets")[1]["datasets"] == [payload["dataset"]]


def test_label_route_errors(monkeypatch, workflow):
    service, _, _ = workflow
    monkeypatch.setattr(
        local_api, "SERVICE", SimpleNamespace(_label_draft_service=lambda: service)
    )
    status, body = request("GET", "/v1/labels/jobs/missing")
    assert status == 404 and body["error"]["code"] == "JOB_NOT_FOUND"
    assert request("GET", "/v1/labels/drafts/%2e%2e%2fother")[0] == 404
    status, body = request(
        "POST",
        "/v1/labels/jobs",
        {
            "task": TASK,
            "source_ids": [SOURCE_ID, SOURCE_ID],
            "language": "en",
        },
    )
    assert status == 400 and body["error"]["code"] == "INVALID_SOURCES"


def test_judge_audit_import_and_all_disagree(monkeypatch, workflow):
    service, cli, _ = workflow
    monkeypatch.setattr(
        local_api, "SERVICE", SimpleNamespace(_label_draft_service=lambda: service)
    )
    gold, _ = load_gold(GOLD, MANIFEST)
    predictions = load_predictions(PREDICTIONS, gold)
    judgments = load_judgments(JUDGMENTS, gold, predictions)
    sample = audit_sample(gold, predictions, judgments)
    rows = [
        next(row for row in sample if row["kind"] == "match" and row["language"] == "en"),
        next(row for row in sample if row["kind"] == "faithfulness" and row["language"] == "ja"),
    ]
    path = "/v1/labels/judge-audits"
    assert request("POST", path, {"judgments": [rows[0], rows[0]]})[0] == 400
    status, payload = request("POST", path, {"judgments": rows})
    assert status == 201 and not cli.calls
    draft = payload["draft"]
    assert draft["language"] == "mixed" and draft["task"] == "judge_audit"
    assert len(draft["items"]) == 2
    for source in draft["sources"]:
        expected = service.source_getter(source["source_id"])
        if isinstance(expected, dict):
            assert source["title"] == expected["title"] and source["playback"]
        else:
            assert source["title"] == source["source_id"] and source["playback"] is None
    draft_path = f"/v1/labels/drafts/{draft['draft_id']}"
    item = next(item for item in draft["items"] if item["judgment"]["kind"] == "faithfulness")
    status, payload = request("GET", f"{draft_path}/context?item={item['id']}")
    assert status == 200 and payload["context"]["lines"] == item["judgment"]["window"]
    assert request("POST", f"{draft_path}/finalize", {})[0] == 409
    first = draft["items"][0]["id"]
    assert request("POST", f"{draft_path}/review", {
        "decisions": [{"id": first, "decision": "edited", "edits": {"text": "changed"}}]
    })[0] == 400
    decisions = [{"id": item["id"], "decision": "rejected", "note": "Fixture disagreement."}
                 for item in draft["items"]]
    assert request("POST", f"{draft_path}/review", {"decisions": decisions})[0] == 200
    status, payload = request("POST", f"{draft_path}/finalize", {})
    assert status == 200 and payload["dataset"]["agreement_rate"] == 0
    assert payload["dataset"]["row_count"] == 2
    data_path, manifest = service._dataset_paths(draft)
    assert manifest.is_file()
    saved = [json.loads(line) for line in data_path.read_bytes().splitlines()]
    assert saved[0]["review_note"] == "Fixture disagreement."
    status, payload = request("POST", path, {"judgments": rows})
    assert status == 201 and payload["draft"]["draft_id"] == draft["draft_id"]
    assert payload["draft"]["agreement_rate"] == 0


@pytest.mark.parametrize(
    "path",
    [
        "/v1/labels/jobs",
        "/v1/labels/judge-audits",
        "/v1/labels/drafts/example/review",
        "/v1/labels/drafts/example/finalize",
    ],
)
def test_label_post_requires_browser_session(monkeypatch, path):
    def unexpected():
        pytest.fail("A label route ran without a browser session")

    monkeypatch.setattr(
        local_api, "SERVICE", SimpleNamespace(_label_draft_service=unexpected)
    )
    client, server = socket.socketpair()
    with client, server:
        client.sendall(
            (
                f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n"
                "Origin: http://127.0.0.1:8000\r\nContent-Type: application/json\r\n"
                "Content-Length: 2\r\n\r\n{}"
            ).encode("ascii")
        )
        client.shutdown(socket.SHUT_WR)
        local_api.Handler(
            server,
            ("127.0.0.1", 50000),
            SimpleNamespace(server_address=("127.0.0.1", 8000)),
        )
        assert client.recv(4096).startswith(b"HTTP/1.0 403")


def test_audit_draft_picks_up_sources_indexed_after_import(workflow):
    service, _, source = workflow
    gold, _ = load_gold(GOLD, MANIFEST)
    predictions = load_predictions(PREDICTIONS, gold)
    rows = audit_sample(gold, predictions, load_judgments(JUDGMENTS, gold, predictions))[:2]
    draft = service.import_judge_audit(rows)
    assert all(row["playback"] is None for row in draft["sources"])
    first = draft["items"][0]["id"]
    service.save_review(draft["draft_id"], [{"id": first, "decision": "approved"}])

    indexed = {row["source_id"]: {**source, "title": f"Indexed {row['source_id']}"}
               for row in draft["sources"]}
    service.source_getter = indexed.get
    for reopened in (service.get_draft(draft["draft_id"]), service.import_judge_audit(rows)):
        assert reopened["draft_id"] == draft["draft_id"]
        assert reopened["items"][0]["review"]["decision"] == "approved"
        for row in reopened["sources"]:
            assert row["title"] == f"Indexed {row['source_id']}" and row["playback"]
