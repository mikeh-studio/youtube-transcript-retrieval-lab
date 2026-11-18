"""Feed metadata and pinned HTTP transport tests with synthetic replies."""

import io
import ipaddress
import socket
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from local_preview import feeds


RSS = b"""<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel><title>Paper Radio</title><language>en</language><link>https://radio.example/</link>
<item><guid>fold-1</guid><title>Fold a boat</title><link>/boat</link>
<pubDate>Wed, 18 Nov 2026 20:00:00 +0800</pubDate><itunes:duration>01:02:03</itunes:duration>
<enclosure url="/boat.mp3" length="1234" type="audio/mpeg"/></item>
</channel></rss>"""

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"
 xmlns:yt="http://www.youtube.com/xml/schemas/2015" xml:lang="ja">
<title>Paper Channel</title><link href="https://video.example/channel"/>
<entry><id>yt:video:synthetic01</id><yt:videoId>synthetic01</yt:videoId>
<title>Paper bird</title><published>2026-11-18T12:00:00Z</published>
<link href="https://video.example/bird"/>
<link rel="enclosure" href="https://example.test/bird.mp3" type="audio/mpeg"/></entry>
<entry xml:base="https://media.example/"><id>paper-plane</id><title>Paper plane</title>
<link rel="enclosure" href="plane.ogg" type="audio/ogg" length="111"/></entry></feed>"""


def test_rss_enclosure():
    assert feeds.parse_feed(RSS, base_url="https://radio.example/feed") == {
        "title": "Paper Radio", "language": "en", "link": "https://radio.example/",
        "items": [{
            "external_id": "fold-1", "title": "Fold a boat",
            "published_at": "2026-11-18T12:00:00+00:00", "duration_seconds": 3723,
            "media_url": "https://radio.example/boat.mp3", "media_type": "audio/mpeg",
            "media_length_bytes": 1234, "link": "https://radio.example/boat",
        }],
    }


def test_atom_supports_youtube_ids_and_podcast_enclosures():
    feed = feeds.parse_feed(ATOM)
    assert (feed["title"], feed["language"], feed["link"]) == (
        "Paper Channel", "ja", "https://video.example/channel"
    )
    video, episode = feed["items"]
    assert video["external_id"] == "synthetic01"
    assert video["published_at"] == "2026-11-18T12:00:00+00:00"
    assert video["link"] == "https://video.example/bird"
    assert video["media_url"] is None
    assert episode["external_id"] == "paper-plane"
    assert episode["media_url"] == "https://media.example/plane.ogg"
    assert episode["media_length_bytes"] == 111


def test_feed_preview_only_accepts_the_metadata_feed_on_youtube_hosts():
    def fetch(url, **options):
        options["check_url"](feeds.split_http_url(url))
        return SimpleNamespace(body=ATOM, url=url)

    url = "https://www.youtube.com/feeds/videos.xml?channel_id=synthetic"
    feed = feeds.preview_feed(url, fetch=fetch)
    assert feed["items"][0]["external_id"] == "synthetic01"
    with pytest.raises(ValueError, match="Atom feed URL"):
        feeds.preview_feed("https://video.googlevideo.com/synthetic.mp3", fetch=fetch)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-be", "utf-32"])
def test_rejects_doctype_or_entity(encoding):
    xml = '<!DOCTYPE rss [<!ENTITY note "synthetic">]><rss><channel><title>&note;</title></channel></rss>'
    with pytest.raises(ValueError, match="DOCTYPE or ENTITY"):
        feeds.parse_feed(xml.encode(encoding))


def _addresses(*ips):
    return [
        (socket.AF_INET6 if ipaddress.ip_address(ip).version == 6 else socket.AF_INET,
         socket.SOCK_STREAM, 6, "", (ip, 443))
        for ip in ips
    ]


def _reply(status, headers=None, body=b"", length=None):
    return SimpleNamespace(
        status=status, headers=headers or {}, length=length, read1=io.BytesIO(body).read1
    )


def _connection(response):
    conn = Mock()
    conn.getresponse.return_value = nullcontext(response)
    return conn


def _fetch(url="https://radio.example/feed", **options):
    return feeds.safe_fetch(url, max_bytes=8, timeout=1, accept="text/xml", **options)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "192.168.1.1", "169.254.169.254", "224.0.0.1", "240.0.0.1",
    "::1", "fc00::1", "fe80::1", "fec0::1", "ff02::1", "::ffff:127.0.0.1",
])
def test_rejects_any_non_public_dns_result(address):
    connector = Mock()
    with pytest.raises(ValueError, match="non-public"):
        _fetch(resolver=Mock(return_value=_addresses("8.8.8.8", address)), connect=connector)
    connector.assert_not_called()


@pytest.mark.parametrize("url", ["file:///test", "http://user@radio.example", "http://radio.example:0"])
def test_rejects_invalid_urls_before_resolving(url):
    resolver = Mock()
    with pytest.raises(ValueError, match="HTTP"):
        _fetch(url, resolver=resolver)
    resolver.assert_not_called()


def test_resolves_every_hop_and_reuses_only_checked_addresses():
    first = _connection(_reply(302, {"Location": "/second"}))
    resolver = Mock(side_effect=[_addresses("8.8.8.8"), _addresses("127.0.0.1")])
    connect = Mock(return_value=first)
    with pytest.raises(ValueError, match="non-public"):
        _fetch(resolver=resolver, connect=connect)
    assert resolver.call_count == 2
    assert connect.call_args.args[1] == _addresses("8.8.8.8")
    first.close.assert_called_once()


def test_connection_uses_pinned_sockaddr_and_preserves_tls_hostname(monkeypatch):
    addresses = _addresses("8.8.8.8")
    sock = Mock()
    monkeypatch.setattr(feeds.socket, "socket", Mock(return_value=sock))
    conn = feeds._connect(feeds.split_http_url("https://radio.example/feed"), addresses, 5)
    assert conn.host == "radio.example"
    assert conn._create_connection(("radio.example", 443), 5, None) is sock
    sock.connect.assert_called_once_with(("8.8.8.8", 443))


def test_follows_five_redirects_and_sends_conditional_headers():
    connections = [
        _connection(_reply(302, {"Location": f"/hop-{index}"})) for index in range(5)
    ] + [_connection(_reply(304, {"ETag": '"synth"'}))]
    headers = {"If-None-Match": '"synth"', "If-Modified-Since": "Wed, 18 Nov 2026 12:00:00 GMT"}
    result = _fetch(
        resolver=Mock(return_value=_addresses("8.8.8.8")),
        connect=Mock(side_effect=connections),
        headers=headers,
    )
    assert (result.status, result.body, result.url) == (304, b"", "https://radio.example/hop-4")
    assert result.headers["ETag"] == '"synth"'
    for conn in connections:
        assert conn.request.call_args.kwargs["headers"]["If-None-Match"] == '"synth"'
        assert conn.request.call_args.kwargs["headers"]["If-Modified-Since"] == headers["If-Modified-Since"]
        conn.close.assert_called_once()


def test_redirect_limit():
    connect = Mock(side_effect=[_connection(_reply(301, {"Location": "/again"})) for _ in range(6)])
    with pytest.raises(ValueError, match="too many redirects"):
        _fetch(resolver=Mock(return_value=_addresses("8.8.8.8")), connect=connect)
    assert connect.call_count == 6


@pytest.mark.parametrize("length", [None, 9])
def test_byte_cap_with_and_without_length_header(length):
    with pytest.raises(ValueError, match="size limit"):
        _fetch(
            resolver=Mock(return_value=_addresses("8.8.8.8")),
            connect=Mock(return_value=_connection(_reply(200, body=b"123456789", length=length))),
        )
