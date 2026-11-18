"""Exercise the real label routes with a fake drafter and synthetic transcript."""

import json
import socket
from types import SimpleNamespace

import pytest

from tests.test_eval_generator_api import _request, local_api
from tests.test_label_drafts import SOURCE_ID, TASK, wait_for_job
from tests.test_label_drafts import workflow as workflow


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


@pytest.mark.parametrize(
    "path",
    [
        "/v1/labels/jobs",
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
