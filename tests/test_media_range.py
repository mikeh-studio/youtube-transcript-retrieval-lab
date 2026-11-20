"""HTTP tests for registered retained media and byte ranges."""

import threading
from types import SimpleNamespace

import pytest

from tests.test_eval_generator_api import _request, local_api

SOURCE_ID = "pod_0123456789abcdef"


@pytest.fixture
def media_service(tmp_path, monkeypatch):
    data = tmp_path / "data"
    media = data / "media"
    media.mkdir(parents=True)
    path = media / "paper.m4a"
    path.write_bytes(b"0123456789")
    source = {"platform": "podcast_rss", "local_media_path": "media/paper.m4a"}
    library = SimpleNamespace(data_dir=data, videos={SOURCE_ID: {"source": source}})
    service = SimpleNamespace(
        engine=SimpleNamespace(library=library), library_write_lock=threading.RLock()
    )
    monkeypatch.setattr(local_api, "SERVICE", service)
    return path, source


def _media(range_header=None, source_id=SOURCE_ID):
    return _request(
        "GET", f"/v1/sources/{source_id}/media",
        request_headers={"Range": range_header} if range_header else {},
    )


def test_full_media(media_service):
    status, headers, body = _media()
    assert status == 200 and body == b"0123456789"
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Length"] == "10"
    assert headers["Content-Type"] == "audio/mp4"
    assert "Content-Range" not in headers


@pytest.mark.parametrize(("header", "content_range", "body"), [
    ("bytes=2-5", "bytes 2-5/10", b"2345"),
    ("bytes=6-", "bytes 6-9/10", b"6789"),
    ("bytes=-3", "bytes 7-9/10", b"789"),
    ("bytes=-11", "bytes 0-9/10", b"0123456789"),
    ("bytes=8-100", "bytes 8-9/10", b"89"),
])
def test_single_ranges(media_service, header, content_range, body):
    status, headers, received = _media(header)
    assert status == 206 and received == body
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Range"] == content_range
    assert headers["Content-Length"] == str(len(body))


@pytest.mark.parametrize("header", [
    "bytes=100-", "bytes=3-2", "bytes=-0", "bytes=-", "bytes=0-1,4-5", "sample=0-1"
])
def test_invalid_byte_ranges(media_service, header):
    status, headers, body = _media(header)
    assert status == 416 and body == b""
    assert headers["Content-Range"] == "bytes */10"
    assert headers["Accept-Ranges"] == "bytes"


def test_empty_media_does_not_have_a_satisfiable_range(media_service):
    path, _ = media_service
    path.write_bytes(b"")
    status, headers, body = _media("bytes=0-")
    assert status == 416 and body == b""
    assert headers["Content-Range"] == "bytes */0"


def test_serves_only_registered_local_media(media_service):
    path, source = media_service
    outside = path.parent.parent / "outside.m4a"
    outside.write_bytes(b"outside")
    assert _media(source_id="unknown")[0] == 404
    assert _media(source_id="%2e%2e%2foutside")[0] == 404
    source["local_media_path"] = str(outside)
    assert _media()[0] == 404
    symlink = path.parent / "escape.m4a"
    symlink.symlink_to(outside)
    source["local_media_path"] = str(symlink)
    assert _media()[0] == 404
    source["local_media_path"] = str(path)
    source["platform"] = "youtube"
    assert _media()[0] == 404
