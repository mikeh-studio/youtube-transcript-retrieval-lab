"""Offline tests for adapter selection, timestamp output and optional dependencies."""

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import pytest

from local_preview.stt.base import capabilities, get_engine
from local_preview.stt import faster_whisper_engine
from local_preview.stt.faster_whisper_engine import _whisper_model
from local_preview.stt import openai_engine


def test_default_and_configured_capabilities(monkeypatch):
    for setting in ("ENGINE", "MODEL", "DEVICE", "COMPUTE_TYPE", "OPENAI_MODEL", "WORD_TIMESTAMPS"):
        monkeypatch.delenv("YT_RAG_STT_" + setting, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    default = capabilities()
    assert default["default_engine"] == "faster_whisper"
    assert default["model"] == "large-v3-turbo"
    assert default["device"] == "auto"
    assert default["compute_type"] == "int8"
    assert default["word_timestamps"] is True
    assert not default["faster_whisper_importable"]
    assert not default["openai_key_present"]

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setenv("YT_RAG_STT_ENGINE", "openai")
    monkeypatch.setenv("YT_RAG_STT_OPENAI_MODEL", "test-whisper")
    monkeypatch.setenv("YT_RAG_STT_WORD_TIMESTAMPS", "0")
    assert capabilities()["openai_key_present"]
    assert "synthetic-key" not in str(capabilities())
    assert get_engine().model == "test-whisper"
    assert get_engine().word_timestamps is False
    assert get_engine("faster_whisper").model == "large-v3-turbo"
    with pytest.raises(ValueError, match="STT engine"):
        get_engine("unknown")


def test_missing_optional_dependencies(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setitem(sys.modules, "av", None)
    with pytest.raises(RuntimeError, match="requirements-audio.txt"):
        get_engine("faster_whisper").transcribe(Path("synthetic.wav"), "ja")
    with pytest.raises(RuntimeError, match="requirements-audio.txt"):
        get_engine("openai").transcribe(Path("synthetic.wav"), "en")
    monkeypatch.setattr(faster_whisper_engine, "open_audio", lambda _: nullcontext())
    with pytest.raises(RuntimeError, match="faster-whisper"):
        get_engine("faster_whisper").transcribe(Path("synthetic.wav"), "ja")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        openai_engine._openai_client()


def test_faster_whisper_caches_per_configuration(monkeypatch, tmp_path):
    model = Mock()
    word = SimpleNamespace(start=1.25, end=1.5, word="Pear")
    model.transcribe.return_value = (
        [SimpleNamespace(start=1, end=2, text="Pear jam.", words=[word])],
        SimpleNamespace(language="en", duration=2.5),
    )
    constructor = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=constructor))
    source = tmp_path / "synthetic.wav"
    source.write_bytes(b"synthetic audio")
    av_open = Mock(return_value=nullcontext(SimpleNamespace(streams=SimpleNamespace(audio=[1]))))
    monkeypatch.setitem(sys.modules, "av", SimpleNamespace(open=av_open))
    monkeypatch.setenv("YT_RAG_STT_MODEL", "cache-fixture")
    monkeypatch.setenv("YT_RAG_STT_COMPUTE_TYPE", "int8")
    monkeypatch.setenv("YT_RAG_STT_DEVICE", "cpu")
    monkeypatch.setenv("YT_RAG_STT_WORD_TIMESTAMPS", "1")
    try:
        for _ in range(2):
            result = get_engine("faster_whisper").transcribe(source, "en")
        constructor.assert_called_once_with("cache-fixture", device="cpu", compute_type="int8")
        model.transcribe.assert_called_with(
            str(source), language="en", vad_filter=True, word_timestamps=True
        )
        options = av_open.call_args.kwargs
        assert "wav" in options["options"]["format_whitelist"].split(",")
        assert "hls" not in options["options"]["format_whitelist"].split(",")
        with pytest.raises(ValueError, match="single local input"):
            options["io_open"]("https://radio.example/nested.wav", 0, {})
        assert result.segments[0].words == [{"start": 1.25, "end": 1.5, "word": "Pear"}]
        monkeypatch.setenv("YT_RAG_STT_COMPUTE_TYPE", "float32")
        get_engine("faster_whisper").transcribe(source, "en")
        assert constructor.call_count == 2
    finally:
        _whisper_model.cache_clear()


@pytest.mark.parametrize("word_timestamps", [True, False])
def test_openai_wav_chunk_offsets(monkeypatch, word_timestamps):
    monkeypatch.setattr(openai_engine, "CHUNK_SECONDS", 1)
    samples = [b"\x00\x00" * 12000, b"\x01\x00" * 10000]
    requests = []

    def create(*, file, **options):
        assert Path(file.name).stat().st_mode & 0o777 == 0o600
        with wave.open(file, "rb") as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
            requests.append((options, wav.readframes(wav.getnframes())))
        return SimpleNamespace(
            segments=[SimpleNamespace(start=0.05, end=0.25, text="A green pear.")],
            words=[SimpleNamespace(start=0.15, end=0.25, word="pear")],
        )

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    engine = openai_engine.OpenAIEngine(
        model="whisper-1", word_timestamps=word_timestamps, client=client,
        decoder=lambda _: iter(samples),
    )
    result = engine.transcribe(Path("synthetic.wav"), "en")
    assert result.duration == 1.375
    assert result.word_timestamps == word_timestamps
    assert [row.start for row in result.segments] == [0.05, 1.05]
    assert [row.end for row in result.segments] == [0.25, 1.25]
    assert result.segments[1].words == (
        [{"start": 1.15, "end": 1.25, "word": "pear"}] if word_timestamps else None
    )
    assert [len(data) for _, data in requests] == [32000, 12000]
    assert b"".join(data for _, data in requests) == b"".join(samples)
    for options, _ in requests:
        assert options == {
            "model": "whisper-1", "response_format": "verbose_json", "language": "en",
            "timestamp_granularities": ["segment", "word"] if word_timestamps else ["segment"],
        }
