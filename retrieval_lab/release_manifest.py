"""One release-file classification shared by artifact audits and provenance."""

from pathlib import PurePosixPath

MANIFEST_PATH = "retrieval_lab/public-files.txt"
SOURCE_ONLY = frozenset(
    {
        "pyproject.toml",
        "MANIFEST.in",
        "README.md",
        "LICENSE",
        "scripts/check_distribution.py",
        "scripts/smoke_installed.py",
    }
)


def parse_manifest(text: str) -> set[str]:
    """Reject duplicate, empty, absolute, or traversing manifest entries."""
    entries = text.splitlines()
    if not entries:
        raise ValueError("Empty public manifest")
    if len(entries) != len(set(entries)):
        raise ValueError("Duplicate public manifest entry")
    for entry in entries:
        path = PurePosixPath(entry)
        if (
            not entry
            or path.is_absolute()
            or ".." in path.parts
            or str(path) != entry
            or "\\" in entry
        ):
            raise ValueError(f"Invalid public manifest path: {entry}")
    return set(entries)


def runtime_files(allowed: set[str]) -> set[str]:
    """Every non-source-only entry is required in the wheel and its identity."""
    return allowed - SOURCE_ONLY
