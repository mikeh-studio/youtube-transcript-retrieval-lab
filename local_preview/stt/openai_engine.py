"""Decode audio locally and transcribe bounded WAV requests with OpenAI."""

import os
import tempfile
import wave
from bisect import bisect_right
from contextlib import ExitStack
from pathlib import Path

from .audio_input import open_audio
from .base import TranscriptionResult, TranscriptionSegment

SAMPLE_RATE = 16_000
SAMPLE_BYTES = 2
CHUNK_SECONDS = 600


def _decode_pcm(path):
    with open_audio(path) as (av, container):
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(audio=0):
            for pcm in resampler.resample(frame):
                yield bytes(pcm.planes[0])[:pcm.samples * SAMPLE_BYTES]
        for pcm in resampler.resample(None):
            yield bytes(pcm.planes[0])[:pcm.samples * SAMPLE_BYTES]


def _pcm_chunks(frames):
    limit = CHUNK_SECONDS * SAMPLE_RATE * SAMPLE_BYTES
    buffer = bytearray()
    for frame in frames:
        pending = memoryview(frame)
        while pending:
            count = min(limit - len(buffer), len(pending))
            buffer.extend(pending[:count])
            pending = pending[count:]
            if len(buffer) == limit:
                yield buffer
                buffer = bytearray()
    if buffer:
        yield buffer


def _openai_client():
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise ValueError("Set OPENAI_API_KEY to use OpenAI speech-to-text.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt to use OpenAI speech-to-text.") from exc
    return OpenAI()


def _segments(response, offset, with_words):
    rows = sorted(response.segments or [], key=lambda row: row.start)
    segments = [
        TranscriptionSegment(
            offset + row.start, offset + row.end, row.text, [] if with_words else None
        )
        for row in rows
    ]
    if with_words and rows:
        starts = [row.start for row in rows]
        for word in response.words or []:
            index = max(0, bisect_right(starts, word.start) - 1)
            segments[index].words.append({
                "start": offset + word.start,
                "end": offset + word.end,
                "word": word.word,
            })
    return segments


class OpenAIEngine:
    def __init__(self, *, model, word_timestamps, client=None, decoder=_decode_pcm):
        self.model = model
        self.word_timestamps = word_timestamps
        self.client = client
        self.decoder = decoder

    def transcribe(self, path, language):
        segments = []
        sample_offset = 0
        granularity = ["segment", "word"] if self.word_timestamps else ["segment"]
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            frames = iter(self.decoder(Path(path)))
            if close := getattr(frames, "close", None):
                stack.callback(close)
            for pcm in _pcm_chunks(frames):
                if len(pcm) % SAMPLE_BYTES:
                    raise ValueError("Decoded audio contains a partial PCM sample.")
                if self.client is None:
                    self.client = _openai_client()
                with tempfile.NamedTemporaryFile(dir=directory, suffix=".wav") as chunk:
                    with wave.open(chunk, "wb") as output:
                        output.setparams((1, SAMPLE_BYTES, SAMPLE_RATE, 0, "NONE", "not compressed"))
                        output.writeframes(pcm)
                    chunk.seek(0)
                    try:
                        response = self.client.audio.transcriptions.create(
                            file=chunk.file,
                            model=self.model,
                            response_format="verbose_json",
                            timestamp_granularities=granularity,
                            language=language,
                        )
                    except Exception as exc:
                        raise RuntimeError("OpenAI transcription request failed.") from exc
                segments.extend(_segments(response, sample_offset / SAMPLE_RATE, self.word_timestamps))
                sample_offset += len(pcm) // SAMPLE_BYTES

        return TranscriptionResult(
            segments, language, sample_offset / SAMPLE_RATE,
            "openai", self.model, self.word_timestamps,
        )
