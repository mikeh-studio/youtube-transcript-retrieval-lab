"""Speech-to-text contract and lazy engine selection."""

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict


class TranscriptionWord(TypedDict):
    start: float
    end: float
    word: str


@dataclass
class TranscriptionSegment:
    start: float
    end: float
    text: str
    words: list[TranscriptionWord] | None = None


@dataclass
class TranscriptionResult:
    segments: list[TranscriptionSegment]
    language: str
    duration: float
    engine: str
    model: str
    word_timestamps: bool


class TranscriptionEngine(Protocol):
    def transcribe(self, path: Path, language: str) -> TranscriptionResult: ...


def engine_name(name=None):
    """Resolve and validate the configured or requested engine."""
    name = os.environ.get("YT_RAG_STT_ENGINE", "faster_whisper") if name is None else name
    if name not in ("faster_whisper", "openai"):
        raise ValueError("STT engine must be faster_whisper or openai.")
    return name


def _settings():
    return {
        "model": os.environ.get("YT_RAG_STT_MODEL", "large-v3-turbo"),
        "compute_type": os.environ.get("YT_RAG_STT_COMPUTE_TYPE", "int8"),
        "device": os.environ.get("YT_RAG_STT_DEVICE", "auto"),
        "openai_model": os.environ.get("YT_RAG_STT_OPENAI_MODEL", "whisper-1"),
        "word_timestamps": os.environ.get("YT_RAG_STT_WORD_TIMESTAMPS", "1") == "1",
    }


def capabilities():
    """Describe installed packages and configuration without loading a model."""
    return {
        "default_engine": os.environ.get("YT_RAG_STT_ENGINE", "faster_whisper"),
        "faster_whisper_importable": importlib.util.find_spec("faster_whisper") is not None,
        "av_importable": importlib.util.find_spec("av") is not None,
        "openai_key_present": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        **_settings(),
    }


def get_engine(name=None):
    """Construct the selected adapter without importing optional packages."""
    name = engine_name(name)
    settings = _settings()
    if name == "faster_whisper":
        from .faster_whisper_engine import FasterWhisperEngine

        return FasterWhisperEngine(
            model=settings["model"],
            device=settings["device"],
            compute_type=settings["compute_type"],
            word_timestamps=settings["word_timestamps"],
        )

    from .openai_engine import OpenAIEngine

    return OpenAIEngine(
        model=settings["openai_model"], word_timestamps=settings["word_timestamps"]
    )
