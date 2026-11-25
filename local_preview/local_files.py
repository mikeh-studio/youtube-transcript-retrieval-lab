"""Atomic publication of local evaluation artifacts."""

import json
import tempfile
from pathlib import Path


def json_bytes(payload):
    return (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def write_atomic(path, content, *, overwrite=True):
    """Publish complete owner-only bytes, optionally requiring an unused path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            temp_path.chmod(0o600)
            temp.write(content)
            temp.close()
            if overwrite:
                temp_path.replace(path)
            else:
                path.hardlink_to(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
