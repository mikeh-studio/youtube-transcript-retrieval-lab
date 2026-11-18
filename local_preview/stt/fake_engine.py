"""Injected speech-to-text adapter for offline tests."""


class FakeEngine:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def transcribe(self, path, language):
        self.calls.append((path, language))
        return self.result
