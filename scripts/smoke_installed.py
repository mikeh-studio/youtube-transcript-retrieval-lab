"""Exercise an installed wheel from an unrelated directory and disposable store.

This seeds synthetic evidence locally; it does not verify live YouTube ingestion.
Invoke with the clean environment's Python and an empty --output-dir outside the
source checkout. No provider calls, model downloads, or existing data are used.
"""

import argparse
import hashlib
import importlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import HTTPCookieProcessor, Request, build_opener


def checksums(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.chdir(root)
    sys.path = [
        p
        for p in sys.path
        if p and Path(p).resolve() != Path(__file__).resolve().parent
    ]
    os.environ.update(
        YT_RAG_WORKSPACE=str(root),
        YT_RAG_DATA_DIR=str(root / "data"),
        YT_RAG_FORCE_HASH_EMBEDDINGS="1",
        HF_HUB_OFFLINE="1",
    )
    os.environ.pop("PYTHONPATH", None)
    from local_preview.config import RuntimeConfig
    from local_preview.local_api import LocalRAGService
    from retrieval_lab.provenance import implementation_manifest
    import local_preview

    if "site-packages" not in str(local_preview.__file__):
        raise AssertionError(
            "Smoke test must use installed package, not source checkout"
        )
    identity = implementation_manifest()
    imported = []
    for name in sorted(identity["files"]):
        if not name.endswith(".py"):
            continue
        module_name = (
            name.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        )
        module = importlib.import_module(module_name)
        assert "site-packages" in str(module.__file__), module_name
        imported.append(module_name)
    config = RuntimeConfig.resolve()
    service = LocalRAGService(config=config)
    generator_schema = service._eval_generator_service().schema_path
    assert generator_schema.is_file() and not generator_schema.is_relative_to(root)
    assert json.loads(generator_schema.read_text())["type"] == "object"
    library = service.engine.library
    transcript = [
        {"text": text, "start": i * 35.0, "duration": 30.0}
        for i, text in enumerate(
            [
                "A solar panel converts sunlight into electricity.",
                "Battery storage keeps the electricity available at night.",
                "An inverter converts battery power for household appliances.",
                "Solar electricity and battery storage reduce grid demand.",
            ]
        )
    ]
    library._fetch_transcript = lambda *_: transcript
    library._fetch_title = lambda *_: "Synthetic solar energy lesson"
    library.add_video("https://www.youtube.com/watch?v=SolarDemo01", "en")
    library.save()
    before = checksums(config.data_dir)
    snapshot = root / "snapshot"
    shutil.copytree(config.data_dir, snapshot)
    first = service.retrieve("solar electricity battery", k=3, retrieval_mode="hybrid")
    restored = LocalRAGService(config=config).retrieve(
        "solar electricity battery", k=3, retrieval_mode="hybrid"
    )
    key = lambda rows: [
        (r["video_id"], r["chunk_index"], r["text"]) for r in rows["results"]
    ]
    assert key(first) == key(restored) and first["results"]
    assert checksums(config.data_dir) == before
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    with (root / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "retrieval_lab.cli",
                "--port",
                str(port),
                "--workspace",
                str(root),
                "--data-dir",
                str(config.data_dir),
            ],
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(120):
                if process.poll() is not None:
                    raise AssertionError("Installed server exited; inspect server.log")
                try:
                    session = json.load(
                        client.open(url + "/browser-session", timeout=1)
                    )
                    break
                except OSError:
                    time.sleep(0.25)
            else:
                raise AssertionError("Installed server did not start")
            for page in (
                "index.html",
                "assets/index.js",
                "catalog.html",
                "evaluation.html",
                "reviews.html",
                "evidence.html",
                "chunking.html",
                "video_detail.html",
                "browser-session.js",
                "lab-nav.js",
            ):
                assert client.open(url + "/" + page).status == 200, page
            videos = json.load(client.open(url + "/v1/videos"))
            request = Request(
                url + "/v1/search",
                data=json.dumps(
                    {
                        "query": "solar electricity battery",
                        "k": 3,
                        "retrieval_mode": "hybrid",
                    }
                ).encode(),
                headers={
                    "Origin": url,
                    "Content-Type": "application/json",
                    "X-CSRF-Token": session["csrf_token"],
                },
            )
            search = json.load(client.open(request))
            assert search["results"]
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    # Rehearse restoring a disposable copy, never deleting a user data directory.
    rollback = root / "rollback-data"
    shutil.copytree(snapshot, rollback)
    assert checksums(rollback) == before
    rollback_config = RuntimeConfig.resolve(
        environ={**os.environ, "YT_RAG_DATA_DIR": str(rollback)}
    )
    assert key(
        LocalRAGService(config=rollback_config).retrieve(
            "solar electricity battery", k=3, retrieval_mode="hybrid"
        )
    ) == key(first)
    report = {
        "installed_module": local_preview.__file__,
        "implementation": implementation_manifest(),
        "installed_imports": imported,
        "synthetic_only": True,
        "videos": videos,
        "search_result_count": len(search["results"]),
        "store_checksums_unchanged_on_reload": True,
        "snapshot_restore_passed": True,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2))
    print("Installed UI, API, synthetic retrieval, reload and snapshot restore passed")


if __name__ == "__main__":
    main()
