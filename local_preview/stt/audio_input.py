"""Open single-file audio inputs with PyAV."""

from contextlib import contextmanager
from pathlib import Path

_FORMATS = "aac,flac,matroska,webm,mov,mp4,m4a,3gp,3g2,mj2,mp3,ogg,wav"


def _deny_nested_input(*args, **kwargs):
    raise ValueError("Audio containers must use a single local input.")


@contextmanager
def open_audio(path):
    """Accept standalone audio or video containers and reject playlist formats."""
    try:
        import av
    except ImportError as exc:
        raise RuntimeError("Install requirements-audio.txt to decode audio.") from exc

    with Path(path).open("rb") as source, av.open(
        source,
        options={"format_whitelist": _FORMATS, "enable_drefs": "0"},
        io_open=_deny_nested_input,
    ) as container:
        if not container.streams.audio:
            raise ValueError("The file has no audio stream.")
        yield av, container
