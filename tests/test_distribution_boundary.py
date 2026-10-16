"""Packaging checks reject accidentally included runtime or private material."""

import importlib.util
from pathlib import Path
import tarfile
import io
import zipfile

import pytest


spec = importlib.util.spec_from_file_location(
    "check_distribution",
    Path(__file__).resolve().parents[1] / "scripts/check_distribution.py",
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

ALLOWED = {"retrieval_lab/__init__.py", checker.MANIFEST_PATH}


def write_archive(path, *, changes=None, omit=(), duplicate=None):
    metadata = checker.METADATA if path.suffix == ".whl" else checker.SDIST_METADATA
    contents = {name: b"" for name in ALLOWED | metadata}
    contents[checker.MANIFEST_PATH] = "\n".join(sorted(ALLOWED)).encode()
    contents.update(changes or {})
    for name in omit:
        contents.pop(name)
    entries = list(contents.items())
    if duplicate:
        entries.append((duplicate, contents[duplicate]))
    if path.suffix == ".whl":
        with zipfile.ZipFile(path, "w") as output:
            for name, data in entries:
                output.writestr(name, data)
    else:
        with tarfile.open(path, "w:gz") as output:
            for name, data in entries:
                item = tarfile.TarInfo(checker.SOURCE_ROOT + "/" + name)
                item.size = len(data)
                output.addfile(item, io.BytesIO(data))


@pytest.mark.parametrize(
    "name",
    [
        ".env.local",
        "local_preview/data/transcript.json",
        "docs/private/notes.md",
        "local_preview/web/review.html",
        "local_preview/new_unreviewed.py",
    ],
)
def test_wheel_requires_explicit_public_file(name, tmp_path):
    archive = tmp_path / "test.whl"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("retrieval_lab/__init__.py", "")
        output.writestr(name, "test sentinel")
    with pytest.raises(ValueError, match="Unexpected wheel member"):
        checker.audit(archive, {"retrieval_lab/__init__.py"})


def test_source_archive_rejects_unreviewed_file(tmp_path):
    archive = tmp_path / "test.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        item = tarfile.TarInfo(checker.SOURCE_ROOT + "/local_preview/data/private.json")
        item.size = 2
        output.addfile(item, io.BytesIO(b"{}"))
    with pytest.raises(ValueError, match="Unexpected source member"):
        checker.audit(archive, ALLOWED)


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz"])
def test_archive_accepts_public_code_and_required_metadata(tmp_path, suffix):
    archive = tmp_path / ("test" + suffix)
    write_archive(archive)
    checker.audit(archive, ALLOWED)


@pytest.mark.parametrize(
    "missing",
    [
        "local_preview/schemas/eval_generator_output.schema.json",
        "local_preview/web/video_detail.html",
    ],
)
def test_wheel_rejects_missing_required_resources(tmp_path, missing):
    archive = tmp_path / "test.whl"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("retrieval_lab/__init__.py", "")
    with pytest.raises(ValueError, match="Missing required public files"):
        checker.audit(archive, {"retrieval_lab/__init__.py", missing})


def test_source_archive_requires_build_inputs(tmp_path):
    archive = tmp_path / "test.tar.gz"
    with tarfile.open(archive, "w:gz"):
        pass
    with pytest.raises(ValueError, match="Missing required public files"):
        checker.audit(archive, {"pyproject.toml"})


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz"])
def test_archive_rejects_tampered_embedded_manifest(tmp_path, suffix):
    archive = tmp_path / ("test" + suffix)
    write_archive(
        archive, changes={checker.MANIFEST_PATH: b"retrieval_lab/__init__.py\n"}
    )
    with pytest.raises(ValueError, match="Embedded public manifest differs"):
        checker.audit(archive, ALLOWED)


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz"])
def test_archive_rejects_duplicate_members(tmp_path, suffix):
    archive = tmp_path / ("test" + suffix)
    if suffix == ".whl":
        with pytest.warns(UserWarning, match="Duplicate name"):
            write_archive(archive, duplicate=checker.MANIFEST_PATH)
    else:
        write_archive(archive, duplicate=checker.MANIFEST_PATH)
    with pytest.raises(ValueError, match="Duplicate"):
        checker.audit(archive, ALLOWED)


@pytest.mark.parametrize("missing", sorted(checker.METADATA))
def test_wheel_requires_each_metadata_file_including_license(tmp_path, missing):
    archive = tmp_path / "test.whl"
    write_archive(archive, omit=(missing,))
    with pytest.raises(ValueError, match="Missing required public files"):
        checker.audit(archive, ALLOWED)


def test_wheel_rejects_wrong_distribution_identity(tmp_path):
    archive = tmp_path / "test.whl"
    write_archive(archive, changes={"other-1.0.dist-info/METADATA": b""})
    with pytest.raises(ValueError, match="Unexpected wheel member"):
        checker.audit(archive, ALLOWED)


@pytest.mark.parametrize(
    "text", ["../private.py", "/private.py", "a.py\na.py", "a//b.py", ""]
)
def test_manifest_rejects_unsafe_or_duplicate_entries(text):
    with pytest.raises(ValueError):
        checker.parse_manifest(text)
