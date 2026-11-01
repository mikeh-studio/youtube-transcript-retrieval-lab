"""Deleting through the service must survive a library reload."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_preview"))
from local_api import LocalRAGService
from multilingual.video_library import VideoLibrary


class EmptyProcessor:
    embed_dim = 128

    def embedding_metadata(self):
        return {"backend": "hashing", "model": "local_hash", "dim": 128}


def test_delete_survives_restart(tmp_path):
    library = VideoLibrary(data_dir=str(tmp_path), processor=EmptyProcessor())
    library.videos["ABCDEFGHIJK"] = {
        "title": "Synthetic empty transcript",
        "url": "https://www.youtube.com/watch?v=ABCDEFGHIJK",
        "language": "en",
        "chunks": [],
    }
    library.save()
    assert (
        "ABCDEFGHIJK" in VideoLibrary(str(tmp_path), processor=EmptyProcessor()).videos
    )
    service = LocalRAGService.__new__(LocalRAGService)
    service.engine = SimpleNamespace(library=library)
    service._mark_video_router_stale = Mock()
    service._delete_ask_history = Mock()
    service.delete_video("ABCDEFGHIJK")
    assert (
        "ABCDEFGHIJK"
        not in VideoLibrary(str(tmp_path), processor=EmptyProcessor()).videos
    )
    service._mark_video_router_stale.assert_called_once_with()
    service._delete_ask_history.assert_called_once_with("ABCDEFGHIJK")
