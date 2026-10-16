"""Reject release files outside the reviewed public distribution manifest."""

import argparse
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from retrieval_lab import __version__
from retrieval_lab.release_manifest import MANIFEST_PATH, parse_manifest, runtime_files

DIST_NAME = "youtube_transcript_retrieval_lab"
DIST_INFO = f"{DIST_NAME}-{__version__}.dist-info"
SOURCE_ROOT = f"{DIST_NAME}-{__version__}"
METADATA = {
    f"{DIST_INFO}/{name}"
    for name in (
        "METADATA",
        "WHEEL",
        "RECORD",
        "entry_points.txt",
        "top_level.txt",
        "licenses/LICENSE",
    )
}
SDIST_METADATA = {
    f"{DIST_NAME}.egg-info/{name}"
    for name in (
        "PKG-INFO",
        "SOURCES.txt",
        "dependency_links.txt",
        "entry_points.txt",
        "requires.txt",
        "top_level.txt",
    )
} | {"PKG-INFO", "setup.cfg"}


def audit(path: Path, allowed: set[str]) -> None:
    """Inspect paths and the embedded manifest without extracting an archive."""
    parse_manifest("\n".join(sorted(allowed)))
    names = set()
    embedded = None
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            all_names = [member.filename for member in members]
            if len(all_names) != len(set(all_names)):
                raise ValueError("Duplicate wheel member")
            for member in members:
                name = member.filename
                if member.is_dir():
                    parse_manifest(name.rstrip("/"))
                    continue
                parse_manifest(name)
                if name not in runtime_files(allowed) | METADATA:
                    raise ValueError(f"Unexpected wheel member: {name}")
                names.add(name)
                if name == MANIFEST_PATH:
                    embedded = archive.read(member).decode("utf-8")
        required = runtime_files(allowed) | METADATA
    else:
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            all_names = [member.name for member in members]
            if len(all_names) != len(set(all_names)):
                raise ValueError("Duplicate source member")
            for member in members:
                parse_manifest(member.name)
                if member.name == SOURCE_ROOT and member.isdir():
                    continue
                if not member.name.startswith(SOURCE_ROOT + "/"):
                    raise ValueError(f"Unexpected source root: {member.name}")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError(f"Unsupported source member: {member.name}")
                name = member.name[len(SOURCE_ROOT) + 1 :]
                if name not in allowed | SDIST_METADATA:
                    raise ValueError(f"Unexpected source member: {member.name}")
                names.add(name)
                if name == MANIFEST_PATH:
                    embedded = archive.extractfile(member).read().decode("utf-8")
        required = allowed | SDIST_METADATA
    missing = required - names
    if missing:
        raise ValueError("Missing required public files: " + ", ".join(sorted(missing)))
    if embedded is None or parse_manifest(embedded) != allowed:
        raise ValueError("Embedded public manifest differs from reviewed manifest")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", type=Path, nargs="+")
    args = parser.parse_args()
    allowed = parse_manifest((ROOT / MANIFEST_PATH).read_text())
    for archive in args.archives:
        audit(archive, allowed)
        print(f"Public distribution verified: {archive.name}")


if __name__ == "__main__":
    main()
