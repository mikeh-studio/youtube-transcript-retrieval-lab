"""Public feed fetching and RSS/Atom parsing."""

import http.client
import ipaddress
import re
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

FEED_MAX_BYTES = 5 * 1024 * 1024
FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.1"
REDIRECTS = {301, 302, 303, 307, 308}
YOUTUBE_DOMAINS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "googlevideo.com")
ATOM = "{http://www.w3.org/2005/Atom}"
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
YOUTUBE = "{http://www.youtube.com/xml/schemas/2015}"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"


def split_http_url(url):
    """Validate an HTTP(S) URL before using it as a network destination."""
    if not isinstance(url, str) or not url or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError("A valid HTTP(S) URL is required.")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("A valid HTTP(S) URL is required.") from exc
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None or port == 0
        or any(char in parsed.hostname for char in "%\\")
    ):
        raise ValueError("A valid HTTP(S) URL without credentials is required.")
    return parsed


def is_youtube_url(url):
    """Recognize YouTube page and media hosts from a validated URL."""
    host = url.hostname.rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in YOUTUBE_DOMAINS)


def check_feed_destination(url):
    """Allow YouTube feed URLs while excluding other YouTube endpoints."""
    host = url.hostname.rstrip(".")
    youtube_feed = (
        (host == "youtube.com" or host.endswith(".youtube.com"))
        and url.path == "/feeds/videos.xml"
    )
    if is_youtube_url(url) and not youtube_feed:
        raise ValueError("Use a YouTube Atom feed URL to preview YouTube metadata.")


def _public_addresses(host, port, resolver):
    try:
        addresses = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Could not resolve the URL hostname.") from exc
    if not addresses:
        raise ValueError("Could not resolve the URL hostname.")
    for family, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if isinstance(ip, ipaddress.IPv6Address):
            if ip.sixtofour or ip.teredo or ip.scope_id or ip.is_site_local:
                raise ValueError("URL resolves to a non-public address.")
            ip = ip.ipv4_mapped or ip
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ValueError("URL resolves to a non-public address.")
    return addresses


def _connect(url, addresses, timeout):
    cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    conn = cls(url.hostname, url.port, timeout=timeout)

    def connect_pinned(_address, timeout, source_address):
        for family, socktype, proto, _, address in addresses:
            sock = socket.socket(family, socktype, proto)
            try:
                sock.settimeout(timeout)
                sock.connect(address)
                return sock
            except OSError:
                sock.close()
        raise OSError("Could not connect to any validated address.")

    # Keep the original hostname for the HTTP Host header and TLS verification.
    conn._create_connection = connect_pinned
    return conn


@dataclass
class FetchStream:
    url: str
    status: int
    headers: http.client.HTTPMessage
    _response: http.client.HTTPResponse
    _max_bytes: int
    _content_length: int | None

    def iter_bytes(self):
        """Read bounded blocks, including for responses without a length header."""
        received = 0
        while block := self._response.read1(min(65536, self._max_bytes - received + 1)):
            received += len(block)
            if received > self._max_bytes:
                raise ValueError("Response exceeds the download size limit.")
            yield block
        if self._content_length is not None and received != self._content_length:
            raise ValueError("Response ended before its declared length.")


@dataclass
class FetchResult:
    body: bytes
    url: str
    status: int
    headers: http.client.HTTPMessage


@contextmanager
def open_fetch(
    url, *, max_bytes, timeout, accept, headers=None, check_url=None,
    resolver=None, connect=None,
):
    """Open a size-limited HTTP stream through validated, pinned redirect hops."""
    if max_bytes <= 0:
        raise ValueError("Download size limit must be positive.")
    resolver = socket.getaddrinfo if resolver is None else resolver
    connect = _connect if connect is None else connect
    request_headers = {"Accept": accept, "Accept-Encoding": "identity"}
    for key, value in (headers or {}).items():
        if key.lower() not in {"if-none-match", "if-modified-since"}:
            raise ValueError("Only conditional fetch headers are supported.")
        request_headers[key] = value

    for hop in range(6):
        parsed = split_http_url(url)
        if check_url is not None:
            check_url(parsed)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = _public_addresses(parsed.hostname, port, resolver)
        connection = connect(parsed, addresses, timeout)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        try:
            connection.request("GET", target, headers=request_headers)
            with connection.getresponse() as response:
                if response.status in REDIRECTS:
                    location = response.headers.get("Location")
                    if not location or hop == 5:
                        raise ValueError("Invalid redirect or too many redirects.")
                    url = urljoin(url, location)
                    continue
                if response.status == 304 and headers:
                    yield FetchStream(url, 304, response.headers, response, max_bytes, None)
                    return
                if response.status != 200:
                    raise ValueError(f"Fetch returned HTTP {response.status}.")
                encoding = response.headers.get("Content-Encoding", "identity").lower()
                if encoding != "identity":
                    raise ValueError("Encoded responses are unsupported.")
                length = response.length
                if length is not None and length > max_bytes:
                    raise ValueError("Response exceeds the download size limit.")
                yield FetchStream(url, 200, response.headers, response, max_bytes, length)
                return
        except (OSError, http.client.HTTPException) as exc:
            raise ValueError("Could not download the requested URL.") from exc
        finally:
            connection.close()


def safe_fetch(url, *, max_bytes, timeout, accept, headers=None, **transport):
    """Fetch a bounded response, including its validators for conditional requests."""
    with open_fetch(
        url, max_bytes=max_bytes, timeout=timeout, accept=accept, headers=headers, **transport
    ) as response:
        return FetchResult(
            b"".join(response.iter_bytes()) if response.status != 304 else b"",
            response.url, response.status, response.headers,
        )


def _text(element, tag):
    return (element.findtext(tag) or "").strip() or None


def _url(base, value):
    if value:
        try:
            url = urljoin(base, value.strip())
            split_http_url(url)
            return url
        except ValueError:
            pass
    return None


def _date(value, *, atom):
    if value:
        try:
            date = (
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                if atom else parsedate_to_datetime(value)
            )
            return (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).astimezone(
                timezone.utc
            ).isoformat()
        except (ValueError, OverflowError):
            pass
    return None


def _duration(value):
    if value and re.fullmatch(r"[0-9]{1,10}(?::[0-9]{1,2}){0,2}", value):
        seconds = 0
        for part in value.split(":"):
            seconds = seconds * 60 + int(part)
        return seconds
    return None


def _length(value):
    return int(value) if value and re.fullmatch(r"[0-9]{1,18}", value) else None


def _atom_link(element, rel):
    return next(
        (link for link in element.findall(ATOM + "link") if link.get("rel", "alternate") == rel),
        None,
    )


def _atom_link_url(link, base):
    if link is not None:
        return _url(urljoin(base, link.get(XML_BASE, "")), link.get("href"))
    return None


def _atom_item(item, base):
    link = _atom_link(item, "alternate")
    enclosure = _atom_link(item, "enclosure")
    video_id = _text(item, YOUTUBE + "videoId")
    if video_id:
        enclosure = None
    page = _atom_link_url(link, base)
    media = _atom_link_url(enclosure, base)
    return {
        "external_id": video_id or _text(item, ATOM + "id") or page or media,
        "title": _text(item, ATOM + "title"),
        "published_at": _date(_text(item, ATOM + "published"), atom=True),
        "media_url": media,
        "media_type": enclosure.get("type") if enclosure is not None else None,
        "media_length_bytes": _length(enclosure.get("length")) if enclosure is not None else None,
        "duration_seconds": _duration(_text(item, ITUNES + "duration")),
        "link": page,
    }


def _rss_item(item, base):
    enclosure = item.find("enclosure")
    page = _url(base, _text(item, "link"))
    media = _url(base, enclosure.get("url")) if enclosure is not None else None
    return {
        "external_id": _text(item, "guid") or page or media,
        "title": _text(item, "title"),
        "published_at": _date(_text(item, "pubDate"), atom=False),
        "media_url": media,
        "media_type": enclosure.get("type") if enclosure is not None else None,
        "media_length_bytes": _length(enclosure.get("length")) if enclosure is not None else None,
        "duration_seconds": _duration(_text(item, ITUNES + "duration")),
        "link": page,
    }


def parse_feed(xml_bytes, *, base_url=""):
    """Parse RSS 2.0 or Atom metadata without evaluating XML declarations."""
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", xml_bytes.replace(b"\x00", b""), re.IGNORECASE):
        raise ValueError("Feed XML must not contain DOCTYPE or ENTITY declarations.")
    try:
        root = ElementTree.fromstring(xml_bytes)
    except (ElementTree.ParseError, LookupError, ValueError) as exc:
        raise ValueError("Invalid feed XML.") from exc

    base = urljoin(base_url, root.get(XML_BASE, ""))
    if root.tag == "rss" and (channel := root.find("channel")) is not None:
        base = urljoin(base, channel.get(XML_BASE, ""))
        return {
            "title": _text(channel, "title"),
            "link": _url(base, _text(channel, "link")),
            "language": _text(channel, "language"),
            "items": [
                _rss_item(item, urljoin(base, item.get(XML_BASE, "")))
                for item in channel.findall("item")
            ],
        }
    if root.tag == ATOM + "feed":
        link = _atom_link(root, "alternate")
        return {
            "title": _text(root, ATOM + "title"),
            "link": _atom_link_url(link, base),
            "language": root.get("{http://www.w3.org/XML/1998/namespace}lang"),
            "items": [
                _atom_item(item, urljoin(base, item.get(XML_BASE, "")))
                for item in root.findall(ATOM + "entry")
            ],
        }
    raise ValueError("Expected an RSS or Atom feed.")


def preview_feed(url, *, fetch=safe_fetch):
    """Fetch and parse a feed without storing a subscription."""
    result = fetch(
        url, max_bytes=FEED_MAX_BYTES, timeout=15, accept=FEED_ACCEPT,
        check_url=check_feed_destination,
    )
    return parse_feed(result.body, base_url=result.url)
