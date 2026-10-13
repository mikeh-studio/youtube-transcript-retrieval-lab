"""The public browser accepts only the private service's safe catalog projection."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

import pytest

from local_preview import catalog_client as c


def page():
    return dict(
        generated_at="2026-10-13T00:00:00Z",
        total=1,
        limit=20,
        offset=0,
        scope="private_personal_search",
        items=[
            dict(
                id=str(UUID(int=1)),
                external_id="AAAAAAAAAAA",
                url="https://www.youtube.com/watch?v=AAAAAAAAAAA",
                qualification="not_reviewed",
                metadata={},
                missing_fields={key: "unknown" for key in c.FIELDS},
                labels=[],
                readiness=dict(
                    state="blocked",
                    blockers=["artifact_review_missing"],
                    expires_at=None,
                ),
                searchable="not_checked",
                first_seen=None,
                last_seen=None,
            )
        ],
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:9000",
        "http://localhost:9000",
        "http://example.test:9000",
        "http://127.0.0.1:9000/path",
        "http://user:secret@127.0.0.1:9000",
        "http://127.0.0.1:9000?x=y",
        "http://127.0.0.1:0",
        "http://127.0.0.1:99999",
    ],
)
def test_catalog_rejects_nonexplicit_loopback_base(url):
    with pytest.raises(c.CatalogClientError):
        c.upstream_base(url)


@pytest.mark.parametrize(
    "path,query",
    [
        ("/v1/catalog/runs", ""),
        ("/v1/catalog/items/../runs", ""),
        ("/v1/catalog/items", "limit=0"),
        ("/v1/catalog/items", "limit=101"),
        ("/v1/catalog/items", "limit=1&limit=2"),
        ("/v1/catalog/items", "offset=10001"),
        ("/v1/catalog/items", "url=http://evil.test"),
        ("/v1/catalog/items", "language="),
        ("/v1/catalog/items", "limit=１"),
        ("/v1/catalog/items/" + str(UUID(int=1)), "language=ja"),
    ],
)
def test_catalog_rejects_unexpected_paths_and_parameters(path, query):
    with pytest.raises(c.CatalogClientError):
        c.canonical_request(path, query)


def test_catalog_safe_schema_and_private_field_rejection():
    assert c.validate_response(page(), "/v1/catalog/items") == page()
    for private in [
        "reviewer",
        "reason",
        "artifact_ref",
        "artifact_hash",
        "source_config",
    ]:
        data = page()
        data["items"][0][private] = "PRIVATE_SENTINEL"
        with pytest.raises(ValueError):
            c.validate_response(data, "/v1/catalog/items")
    for field, value in [
        ("url", "javascript:alert(1)"),
        ("searchable", "yes"),
        ("metadata", {"raw": {}}),
    ]:
        data = page()
        data["items"][0][field] = value
        with pytest.raises(ValueError):
            c.validate_response(data, "/v1/catalog/items")


def test_catalog_disabled_external_client_and_capacity_fail_closed(monkeypatch):
    monkeypatch.delenv("CONTENT_LIBRARY_CATALOG_URL", raising=False)
    with pytest.raises(c.CatalogClientError):
        c.fetch_catalog("/v1/catalog/items", "")
    monkeypatch.setenv("CONTENT_LIBRARY_CATALOG_URL", "http://127.0.0.1:9000")
    with pytest.raises(c.CatalogClientError) as error:
        c.fetch_catalog("/v1/catalog/items", "", "10.0.0.1")
    assert error.value.status == 403
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    monkeypatch.setattr(c, "SLOTS", semaphore)
    with pytest.raises(c.CatalogClientError):
        c.fetch_catalog("/v1/catalog/items", "")


def test_catalog_real_http_no_redirect_or_proxy_and_redacted_errors(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        mode = "valid"
        requests = []

        def do_GET(self):
            self.requests.append(dict(self.headers))
            if self.mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://example.test/private")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = page()
            if self.mode == "private":
                payload["items"][0]["reviewer"] = "PRIVATE_SENTINEL"
            if self.mode == "bad_uuid":
                payload["items"][0]["id"] = 7
            body = (
                json.dumps(payload).encode()
                if self.mode != "large"
                else b" " * 2_000_001
            )
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "CONTENT_LIBRARY_CATALOG_URL", f"http://127.0.0.1:{server.server_port}"
    )
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    try:
        assert c.fetch_catalog("/v1/catalog/items", "")["total"] == 1
        assert (
            "Authorization" not in Handler.requests[0]
            and "Cookie" not in Handler.requests[0]
        )
        for mode in ["redirect", "private", "bad_uuid", "large"]:
            Handler.mode = mode
            with pytest.raises(c.CatalogClientError) as error:
                c.fetch_catalog("/v1/catalog/items", "")
            assert error.value.status == 503 and "PRIVATE_SENTINEL" not in str(
                error.value
            )
        assert len(Handler.requests) == 5
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
