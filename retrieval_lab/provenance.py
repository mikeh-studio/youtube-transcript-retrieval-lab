"""Read-only implementation identity for future external experiment consumers."""

import hashlib
from pathlib import Path

from retrieval_lab import __version__
from retrieval_lab.release_manifest import MANIFEST_PATH, parse_manifest, runtime_files


def implementation_manifest() -> dict:
    """Hash public runtime code and resources without loading models or data.

    Consumers must record their own policies, provider/model revisions, datasets,
    and private implementation separately. This identity neither migrates legacy
    results nor authorizes replay of any cached or failed job.
    """
    root = Path(__file__).resolve().parent.parent
    allowed = parse_manifest((root / MANIFEST_PATH).read_text())
    files = [root / name for name in runtime_files(allowed)]
    hashes = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }
    return {
        "schema_version": "retrieval-lab-implementation/v1",
        "distribution": "youtube-transcript-retrieval-lab",
        "version": __version__,
        "files": hashes,
    }
