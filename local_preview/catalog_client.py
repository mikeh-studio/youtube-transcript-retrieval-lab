"""Read-only, loopback-only client for the private catalog projection."""

import ipaddress
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

FIELDS = {
    "title",
    "description",
    "channel_id",
    "channel_name",
    "channel_url",
    "thumbnail_url",
    "duration_ms",
    "published_at",
    "primary_audio_language",
    "metadata_language",
    "platform_category_id",
    "broadcast_status",
}
PARAMS = {
    "limit",
    "offset",
    "channel_id",
    "language",
    "qualification",
    "published_from",
    "published_to",
    "vocabulary",
    "version",
    "term",
    "descendants",
    "scope",
}
EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="catalog-read")
SLOTS = threading.BoundedSemaphore(4)


class CatalogClientError(Exception):
    def __init__(self, status=503):
        self.status = status
        super().__init__(
            "Catalog is unavailable" if status >= 500 else "Invalid catalog request"
        )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def upstream_base(value):
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or not parsed.port
        ):
            raise ValueError()
        return value.rstrip("/")
    except ValueError:
        raise CatalogClientError() from None


def canonical_request(path, query):
    if path != "/v1/catalog/items":
        match = re.fullmatch(r"/v1/catalog/items/([0-9a-fA-F-]{36})", path)
        if not match:
            raise CatalogClientError(400)
        try:
            path = "/v1/catalog/items/" + str(UUID(match[1]))
        except ValueError:
            raise CatalogClientError(400) from None
    pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=20)
    if len({k for k, v in pairs}) != len(pairs):
        raise CatalogClientError(400)
    allowed = PARAMS if path == "/v1/catalog/items" else {"scope"}
    for key, value in pairs:
        if key not in allowed or not value or len(value) > 100:
            raise CatalogClientError(400)
        if key in {"limit", "offset"}:
            if not value.isascii() or not value.isdigit():
                raise CatalogClientError(400)
            if not (
                1 <= int(value) <= 100 if key == "limit" else 0 <= int(value) <= 10000
            ):
                raise CatalogClientError(400)
    return path + ("?" + urlencode(pairs) if pairs else "")


def exact(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise ValueError("shape")


def date(value, nullable=False):
    if nullable and value is None:
        return
    if (
        not isinstance(value, str)
        or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None
    ):
        raise ValueError("timestamp")


def validate_item(item):
    exact(
        item,
        {
            "id",
            "external_id",
            "url",
            "qualification",
            "metadata",
            "missing_fields",
            "labels",
            "readiness",
            "searchable",
            "first_seen",
            "last_seen",
        },
    )
    UUID(item["id"])
    if not isinstance(item["external_id"], str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{11}", item["external_id"]
    ):
        raise ValueError("ID")
    if item["url"] != "https://www.youtube.com/watch?v=" + item["external_id"]:
        raise ValueError("URL")
    if (
        item["qualification"] not in {"not_reviewed", "hold", "eligible", "excluded"}
        or item["searchable"] != "not_checked"
    ):
        raise ValueError("state")
    if not isinstance(item["metadata"], dict) or not set(item["metadata"]) <= FIELDS:
        raise ValueError("metadata")
    for key, field in item["metadata"].items():
        exact(field, {"value", "event_id", "provider", "observed_at", "expires_at"})
        if field["provider"] not in {
            "manual",
            "legacy_inventory",
            "creator_index",
            "youtube_data_api",
        }:
            raise ValueError("provider")
        UUID(field["event_id"])
        date(field["observed_at"])
        date(field["expires_at"], True)
        if key == "duration_ms":
            if type(field["value"]) is not int or field["value"] < 0:
                raise ValueError("duration")
        elif not isinstance(field["value"], str):
            raise ValueError("field")
    missing = item["missing_fields"]
    if (
        not isinstance(missing, dict)
        or set(missing) != FIELDS - set(item["metadata"])
        or not all(v in {"unknown", "expired"} for v in missing.values())
    ):
        raise ValueError("missing")
    if not isinstance(item["labels"], list) or len(item["labels"]) > 500:
        raise ValueError("labels")
    for label in item["labels"]:
        exact(label, {"vocabulary", "version", "key", "namespace", "en", "ja"})
        if not all(isinstance(v, str) and len(v) <= 1000 for v in label.values()):
            raise ValueError("label")
    exact(item["readiness"], {"state", "blockers", "expires_at"})
    date(item["readiness"]["expires_at"], True)
    if item["readiness"]["state"] not in {"blocked", "preconditions_met"}:
        raise ValueError("readiness")
    if not isinstance(item["readiness"]["blockers"], list) or not all(
        isinstance(v, str) and re.fullmatch(r"[a-z_]{1,80}", v)
        for v in item["readiness"]["blockers"]
    ):
        raise ValueError("blockers")
    date(item["first_seen"], True)
    date(item["last_seen"], True)


def validate_response(data, path):
    if path == "/v1/catalog/items":
        exact(data, {"generated_at", "total", "limit", "offset", "scope", "items"})
        if any(
            type(data[k]) is not int or data[k] < 0
            for k in ["total", "limit", "offset"]
        ):
            raise ValueError("count")
        if (
            not 1 <= data["limit"] <= 100
            or data["offset"] > 10000
            or not isinstance(data["items"], list)
            or len(data["items"]) > data["limit"]
        ):
            raise ValueError("page")
        items = data["items"]
    else:
        exact(data, {"generated_at", "scope", "item"})
        items = [data["item"]]
    if data["scope"] != "private_personal_search":
        raise ValueError("scope")
    date(data["generated_at"])
    for item in items:
        validate_item(item)
    return data


def _fetch(base, path, query):
    try:
        request = Request(
            upstream_base(base) + canonical_request(path, query),
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        )
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=3) as response:
            if (
                response.headers.get_content_type() != "application/json"
                or response.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise ValueError("type")
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("size")
        return validate_response(json.loads(raw), path)
    except HTTPError as exc:
        raise CatalogClientError(exc.code if exc.code in {404, 422} else 503) from None
    except (ValueError, TypeError, KeyError, AttributeError, URLError, OSError):
        raise CatalogClientError() from None
    finally:
        SLOTS.release()


def fetch_catalog(path, query, client_host="127.0.0.1"):
    """Bounded GET only; nothing from the caller can select an upstream host."""
    try:
        if not ipaddress.ip_address(client_host).is_loopback:
            raise CatalogClientError(403)
        canonical_request(path, query)
        base = upstream_base(os.environ.get("CONTENT_LIBRARY_CATALOG_URL", ""))
    except ValueError:
        raise CatalogClientError(400) from None
    if not SLOTS.acquire(blocking=False):
        raise CatalogClientError()
    future = EXECUTOR.submit(_fetch, base, path, query)
    try:
        return future.result(timeout=5)
    except FutureTimeout:
        raise CatalogClientError() from None
