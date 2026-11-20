"""Atomic publication of local evaluation artifacts."""

import json
import tempfile
from pathlib import Path


def json_bytes(payload):
    return (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def write_atomic(path, content):
    """Replace a file with complete owner-readable and owner-writable bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            temp_path.chmod(0o600)
            temp.write(content)
            temp.close()
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)
