"""Real HTTP boundary/route tests; provider and disk mutations are stubbed."""

import ast
import http.client
import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from browser_boundary import BrowserBoundary  # noqa: E402


class BrowserBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.effects = []
        if (ROOT / "app.py").exists():
            import app

            app.APP_PASSCODE = ""
            app.add_manual_card = lambda *args: cls.effects.append(args) or {}
            handler = app.FlashcardHandler
            cls.route = "/api/decks/example/cards"
        else:
            # Compile the real handler without initializing models/providers.
            tree = ast.parse((ROOT / "local_preview/local_api.py").read_text())
            node = next(
                n
                for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == "Handler"
            )
            scope = dict(
                BrowserBoundary=BrowserBoundary,
                BaseHTTPRequestHandler=BaseHTTPRequestHandler,
                json=json,
                urlparse=urlparse,
                parse_qs=parse_qs,
                RETRIEVAL_MODES={"hybrid", "dense", "lexical"},
                AGENTIC_RETRIEVAL_ENV="unused",
                _env_flag=lambda _: False,
                _coerce_request_flag=lambda value, default: bool(value or default),
                Path=Path,
                SERVICE=SimpleNamespace(
                    ingest=lambda **kwargs: cls.effects.append(kwargs) or {},
                    delete_search_feedback=lambda **kwargs: (
                        cls.effects.append(kwargs) or 1
                    ),
                    save_search_feedback=lambda body: cls.effects.append(body) or body,
                    retrieve=lambda query, **kwargs: (
                        cls.effects.append(query)
                        or {"retrieval_mode": "hybrid", "details": {}, "results": []}
                    ),
                ),
            )
            exec(
                compile(
                    ast.Module(body=[node], type_ignores=[]), "local_api.py", "exec"
                ),
                scope,
            )
            handler = scope["Handler"]
            cls.route = "/v1/ingest/videos"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = "127.0.0.1:" + str(cls.server.server_port)
        cls.origin = "http://" + cls.host

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method="POST", path=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request(
            method,
            path or self.route,
            body="{}" if method == "POST" else None,
            headers=headers or {},
        )
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def session_headers(self):
        status, headers, body = self.request("GET", "/browser-session")
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        return {
            "Origin": self.origin,
            "Cookie": headers["Set-Cookie"].split(";", 1)[0],
            "X-CSRF-Token": json.loads(body)["csrf_token"],
            "Content-Type": "application/json",
        }

    def test_attacks_never_dispatch(self):
        headers = self.session_headers()
        mutations = [
            {"Origin": None},
            {"Origin": "null"},
            {"Origin": ""},
            {"Origin": "https://untrusted.example"},
            {"Origin": self.origin + "/"},
            {"Origin": "http://localhost:" + str(self.server.server_port)},
            {"Origin": "http://127.0.0.1:1"},
            {"Origin": "malformed"},
            {"Host": "attacker.example:" + str(self.server.server_port)},
            {"Host": "localhost:1"},
            {"Host": "localhost"},
            {"Host": self.host + "@attacker.example"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
            {"Content-Type": "text/plain"},
            {"Content-Type": None},
            {"Content-Type": "application/x-www-form-urlencoded"},
            {"Content-Type": "multipart/form-data; boundary=x"},
            {"X-CSRF-Token": None},
            {"X-CSRF-Token": "wrong"},
            {"Cookie": None},
            {"Cookie": "local_browser_session=forged"},
            {"Forwarded": "host=localhost"},
            {"X-Forwarded-Host": self.host},
            {"X-Forwarded-For": "127.0.0.1"},
            {"Upgrade": "websocket"},
        ]
        for patch in mutations:
            with self.subTest(patch=patch):
                candidate = headers | patch
                candidate = {k: v for k, v in candidate.items() if v is not None}
                count = len(self.effects)
                self.assertIn(self.request(headers=candidate)[0], (403, 415))
                self.assertEqual(len(self.effects), count)

    def test_valid_session_dispatches_once(self):
        count = len(self.effects)
        self.assertIn(self.request(headers=self.session_headers())[0], (200, 201, 202))
        self.assertEqual(len(self.effects), count + 1)

    def test_bootstrap_and_reads_reject_foreign_authority(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            for headers in (
                {"Origin": "https://untrusted.example"},
                {"Host": "evil.example"},
                {"Sec-Fetch-Site": "cross-site"},
                {"Origin": "null"},
            ):
                self.assertEqual(
                    self.request(method, "/browser-session", headers)[0], 403
                )

    def test_other_mutation_methods_require_boundary(self):
        for method in ("DELETE", "PATCH", "PUT"):
            count = len(self.effects)
            self.assertEqual(self.request(method, headers={"Origin": "null"})[0], 403)
            self.assertEqual(len(self.effects), count)

    def test_token_is_bound_to_issued_session(self):
        first = self.session_headers()
        second = self.session_headers()
        first["X-CSRF-Token"] = second["X-CSRF-Token"]
        self.assertEqual(self.request(headers=first)[0], 403)

    def test_bodyless_review_delete_with_session_dispatches_once(self):
        count = len(self.effects)
        status, _, body = self.request(
            "DELETE",
            "/v1/feedback/search-review?label=relevant",
            self.session_headers(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["deleted_count"], 1)
        self.assertEqual(len(self.effects), count + 1)

    def test_review_cli_search_and_feedback_bootstrap_real_sessions(self):
        sys.path.insert(0, str(ROOT / "local_preview"))
        from review_agent_workflow import make_search_runner, make_feedback_submitter

        count = len(self.effects)
        search = make_search_runner(self.origin)({"query": "test query"})
        self.assertTrue(search["ok"])
        feedback = make_feedback_submitter(self.origin)(
            {"query": "test query", "label": "relevant"}
        )
        self.assertTrue(feedback["ok"])
        self.assertEqual(len(self.effects), count + 2)

    def test_unrelated_malformed_cookies_do_not_poison_session(self):
        for foreign in ("other=hello world", 'other="unterminated', "other=bad\\value"):
            for before in (True, False):
                headers = self.session_headers()
                own = headers["Cookie"]
                headers["Cookie"] = (
                    f"{foreign}; {own}" if before else f"{own}; {foreign}"
                )
                with self.subTest(foreign=foreign, before=before):
                    self.assertEqual(self.request(headers=headers)[0], 200)

    def test_duplicate_own_cookie_is_rejected(self):
        headers = self.session_headers()
        headers["Cookie"] += "; " + headers["Cookie"]
        count = len(self.effects)
        self.assertEqual(self.request(headers=headers)[0], 403)
        self.assertEqual(len(self.effects), count)

    def test_duplicate_security_headers(self):
        for name in ("Host", "Origin", "Content-Type", "X-CSRF-Token"):
            conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            conn.putrequest("POST", self.route, skip_host=True)
            for key, value in (self.session_headers() | {"Host": self.host}).items():
                conn.putheader(key, value)
            conn.putheader(name, "evil")
            conn.putheader("Content-Length", "2")
            conn.endheaders(b"{}")
            response = conn.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            conn.close()


if __name__ == "__main__":
    unittest.main()
