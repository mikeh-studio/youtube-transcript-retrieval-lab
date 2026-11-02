"""Exercise real handler error paths without loading models or calling providers."""

import ast
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from browser_boundary import BrowserBoundary


@pytest.fixture
def handler_type():
    tree = ast.parse(
        (Path(__file__).parents[1] / "local_preview/local_api.py").read_text()
    )
    node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Handler"
    )

    def fail(*args, **kwargs):
        raise RuntimeError("provider credential and local file details")

    scope = {
        "BrowserBoundary": BrowserBoundary,
        "BaseHTTPRequestHandler": BaseHTTPRequestHandler,
        "urlparse": urlparse,
        "parse_qs": parse_qs,
        "json": json,
        "EvalGeneratorError": type("EvalGeneratorError", (Exception,), {}),
        "SummaryGenerationError": type("SummaryGenerationError", (Exception,), {}),
        "traceback": SimpleNamespace(print_exc=lambda: None),
        "SERVICE": SimpleNamespace(delete_search_feedback=fail),
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), "local_api.py", "exec"), scope
    )
    return scope["Handler"], fail


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/index.html"),
        ("POST", "/v1/ingest/videos"),
        ("DELETE", "/v1/feedback/search-review"),
    ],
)
def test_internal_exceptions_do_not_expose_details(handler_type, method, path):
    handler, fail = handler_type
    replies = []
    request = SimpleNamespace(
        path=path,
        headers={},
        _read_json_body=fail,
        _serve_static=fail,
        _json=lambda body, status=200: replies.append((status, body)),
    )
    getattr(handler, "do_" + method)(request)
    status, body = replies[0]
    assert status == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "credential" not in body["error"]["message"]
    assert "local file" not in body["error"]["message"]


def test_api_and_static_responses_disable_content_sniffing_and_referrers(handler_type):
    handler, _ = handler_type
    headers = {}
    request = SimpleNamespace(
        send_response=lambda status: None,
        send_header=lambda name, value: headers.update({name: value}),
        end_headers=lambda: None,
    )
    handler._set_headers(request)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
