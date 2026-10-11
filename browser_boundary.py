"""Reject browser-origin attacks before route dispatch (stdlib only)."""

import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from http.cookies import CookieError, SimpleCookie

_KEY = secrets.token_bytes(32)
_COOKIE = "local_browser_session"


class BrowserBoundary:
    """Same-origin session boundary; forwarded authority is never trusted."""

    def _cookie_name(self):
        return f"{_COOKIE}_{self.server.server_address[1]}"

    def trusted_remote_origins(self):
        return set()

    def _boundary_reply(self, status, payload, cookie=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            secure = "; Secure" if self._browser_origin.startswith("https:") else ""
            self.send_header(
                "Set-Cookie",
                f"{self._cookie_name()}={cookie}; Path=/; HttpOnly; SameSite=Strict{secure}",
            )
        self.end_headers()
        self.wfile.write(body)
        return False

    def _session(self):
        try:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
            token = jar[self._cookie_name()].value
            nonce, expires, signature = token.split(".")
            expected = hmac.new(
                _KEY, f"{nonce}.{expires}".encode(), hashlib.sha256
            ).hexdigest()
            if int(expires) > time.time() and hmac.compare_digest(
                signature.encode(), expected.encode()
            ):
                return token
        except (KeyError, ValueError, CookieError):
            pass
        return None

    def parse_request(self):
        if not super().parse_request():
            return False

        def deny(message):
            return self._boundary_reply(403, {"error": message})

        # Duplicate authority/security headers must not have first/last-value ambiguity.
        for name in (
            "Host",
            "Origin",
            "Sec-Fetch-Site",
            "Content-Type",
            "X-CSRF-Token",
            "Cookie",
        ):
            if len(self.headers.get_all(name, [])) > 1:
                return deny("Duplicate security header")
        if any(
            name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")
            for name in self.headers
        ):
            return deny("Forwarded headers are not trusted")
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        authorities = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if port == 80:
            authorities |= {"127.0.0.1", "localhost", "[::1]"}
        remote = self.trusted_remote_origins()
        remote_hosts = {origin.split("://", 1)[1]: origin for origin in remote}
        if host in authorities:
            try:
                if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                    return deny("Loopback client required")
            except ValueError:
                return deny("Loopback client required")
            self._browser_origin = "http://" + host
        elif host in remote_hosts:
            self._browser_origin = remote_hosts[host]
        else:
            return deny("Untrusted Host or port")
        origin = self.headers.get("Origin")
        if origin is not None and origin != self._browser_origin:
            return deny("Same origin required")
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            return deny("Cross-site request rejected")
        if self.headers.get("Upgrade"):
            return deny("Protocol upgrades are unsupported")
        if self.command == "GET" and self.path == "/browser-session":
            token = self._session()
            if not token:
                raw = f"{secrets.token_urlsafe(32)}.{int(time.time()) + 43200}"
                token = (
                    raw + "." + hmac.new(_KEY, raw.encode(), hashlib.sha256).hexdigest()
                )
            return self._boundary_reply(200, {"csrf_token": token}, token)
        if self.command not in {"GET", "HEAD", "OPTIONS"}:
            if origin != self._browser_origin:
                return deny("Origin required for mutations")
            if (
                self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                return self._boundary_reply(415, {"error": "application/json required"})
            token = self._session()
            supplied = self.headers.get("X-CSRF-Token", "")
            if not token or not hmac.compare_digest(token.encode(), supplied.encode()):
                return deny("Valid session CSRF token required")
        return True
