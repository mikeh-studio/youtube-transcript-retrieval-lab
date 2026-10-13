"""Pin shared v1 schemas and the golden fixture against unilateral drift."""

import hashlib
from pathlib import Path


def test_shared_v1_artifact_digests():
    root = Path(__file__).resolve().parents[1]
    expected = [
        (
            "docs/contracts/youtube-library-export-v1-bundle.schema.json",
            "66b0494cbc4d7967e87ab1ddc6c2ff4ac351b0102093c62b1995bcc052e486e8",
        ),
        (
            "docs/contracts/youtube-library-export-v1-record.schema.json",
            "0eeac2f5f44ed540e892b7efc83c048c7e0164576a351eadd6fafd145d4d0558",
        ),
        (
            "tests/fixtures/library_export/v1.json",
            "2b8493acd7c1036038ae43a3e3bce436ea4c1754b797608842076f15e13710c7",
        ),
    ]
    for name, digest in expected:
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
