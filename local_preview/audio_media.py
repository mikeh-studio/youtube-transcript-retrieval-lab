"""Serve byte ranges from registered, locally retained audio sources."""

import os
import re
import stat
from pathlib import Path

from multilingual.source_model import SOURCE_ID_RE

AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
}
VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}
MEDIA_TYPES = AUDIO_TYPES | VIDEO_TYPES


def registered_media_path(library, source_id):
    """Resolve only registered supported media files inside the media directory."""
    if not SOURCE_ID_RE.fullmatch(source_id):
        return None
    source = (library.videos.get(source_id) or {}).get("source") or {}
    value = source.get("local_media_path")
    if source.get("platform") not in {"podcast_rss", "local_audio"} or not value:
        return None

    data_dir = Path(library.data_dir).resolve()
    root = data_dir / "media"
    candidate = data_dir / value
    try:
        path = candidate.resolve(strict=True)
        if (
            root in path.parents and path.suffix.lower() in MEDIA_TYPES
            and not root.is_symlink() and not candidate.is_symlink() and path.is_file()
        ):
            return path
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def byte_range(value, size):
    """Resolve an inclusive single byte range or raise for an invalid range."""
    match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", value.strip(), re.IGNORECASE)
    if not match or not any(match.groups()) or not size:
        raise ValueError("Invalid byte range.")
    first, last = match.groups()
    if not first:
        if int(last) == 0:
            raise ValueError("Empty byte range.")
        return max(0, size - int(last)), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start > end:
        raise ValueError("Unsatisfiable byte range.")
    return start, end


def serve_media(handler, library, lock, source_id):
    """Open a registered file under the write lock, then stream its stable descriptor."""
    with lock:
        path = registered_media_path(library, source_id)
        if path is None:
            return False
        try:
            file = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb")
        except OSError:
            return False
    with file:
        metadata = os.fstat(file.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            return False
        size = metadata.st_size
        status, start, end = 200, 0, size - 1
        ranges = handler.headers.get_all("Range") or []
        if ranges:
            try:
                start, end = byte_range(",".join(ranges), size)
                status = 206
            except ValueError:
                status, start, end = 416, 0, -1

        remaining = end - start + 1
        handler.send_response(status)
        handler.send_header("Content-Type", MEDIA_TYPES[path.suffix.lower()])
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Length", str(remaining))
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Cache-Control", "no-store")
        if status == 206:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        elif status == 416:
            handler.send_header("Content-Range", f"bytes */{size}")
        handler.end_headers()

        file.seek(start)
        try:
            while remaining and (block := file.read(min(65536, remaining))):
                handler.wfile.write(block)
                remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass
    return True
