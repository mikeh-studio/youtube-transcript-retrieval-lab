"""Local faster-whisper adapter."""

from functools import lru_cache

from .audio_input import open_audio
from .base import TranscriptionResult, TranscriptionSegment


@lru_cache(maxsize=None)
def _whisper_model(model, device, compute_type):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install requirements-audio.txt to use faster-whisper.") from exc
    return WhisperModel(model, device=device, compute_type=compute_type)


class FasterWhisperEngine:
    def __init__(self, *, model, device, compute_type, word_timestamps):
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.word_timestamps = word_timestamps

    def transcribe(self, path, language):
        # Check that the input is standalone before passing its path to the decoder.
        with open_audio(path):
            pass
        model = _whisper_model(self.model, self.device, self.compute_type)
        segments, info = model.transcribe(
            str(path), language=language, vad_filter=True, word_timestamps=self.word_timestamps
        )
        rows = [
            TranscriptionSegment(
                segment.start,
                segment.end,
                segment.text,
                [
                    {"start": word.start, "end": word.end, "word": word.word}
                    for word in segment.words or []
                ] if self.word_timestamps else None,
            )
            for segment in segments
        ]
        return TranscriptionResult(
            rows, info.language, info.duration, "faster_whisper", self.model, self.word_timestamps
        )
