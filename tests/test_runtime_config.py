"""Installed paths must not write into code or recover another store's history."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from local_preview.config import RuntimeConfig, load_runtime_config


def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "local_preview").mkdir(parents=True)
    (root / "local_preview/local_api.py").touch()
    (root / "pyproject.toml").touch()
    return root


def test_source_defaults_preserve_checkout_locations(tmp_path):
    root = checkout(tmp_path)
    config = RuntimeConfig.resolve(source_root=root, environ={})
    assert config.workspace_dir == root
    assert config.data_dir == root / "data"
    assert config.env_dir == root
    assert config.static_dir == root / "local_preview/web"
    assert config.legacy_data_dir == root / "local_preview/data"
    assert not config.data_dir.exists()


def test_installed_defaults_never_write_into_distribution(tmp_path):
    code = tmp_path / "site-packages"
    env = {"XDG_DATA_HOME": str(tmp_path / "user-data")}
    config = RuntimeConfig.resolve(source_root=code, environ=env)
    assert (
        config.workspace_dir == tmp_path / "user-data/youtube-transcript-retrieval-lab"
    )
    assert config.data_dir == config.workspace_dir / "data"
    assert config.static_dir == code / "local_preview/web"
    assert config.legacy_data_dir == config.data_dir / "legacy_local_preview"
    assert not config.workspace_dir.exists()


def test_explicit_store_does_not_fall_back_to_checkout_history(tmp_path):
    root = checkout(tmp_path)
    config = RuntimeConfig.resolve(
        source_root=root, environ={"YT_RAG_DATA_DIR": "isolated"}
    )
    assert config.data_dir == root / "isolated"
    assert config.legacy_data_dir == root / "isolated/legacy_local_preview"


def test_dotenv_precedence_and_relative_paths(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("YT_RAG_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("YT_RAG_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("YT_RAG_ENV_DIR", str(tmp_path))
    monkeypatch.setenv("YT_RAG_DATA_DIR", "shell")
    (tmp_path / ".env").write_text("YT_RAG_DATA_DIR=first\n")
    (tmp_path / ".env.local").write_text("YT_RAG_DATA_DIR=second\n")
    # Restore dotenv's mutation as well as the explicit monkeypatch mutations.
    config = load_runtime_config()
    assert config.data_dir == tmp_path / "second"


def test_cli_paths_override_values_loaded_by_application(tmp_path, monkeypatch):
    from retrieval_lab import cli
    from local_preview import config as config_module

    monkeypatch.setattr(config_module, "_startup_config", None)
    called = {}
    monkeypatch.setenv("YT_RAG_DATA_DIR", str(tmp_path / "dotenv-store"))
    module = SimpleNamespace(
        main=lambda argv, **kwargs: called.update(argv=argv, **kwargs) or 0
    )
    import local_preview

    monkeypatch.setattr(local_preview, "local_api", module, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["retrieval-lab", "--data-dir", str(tmp_path / "isolated"), "--port", "8123"],
    )
    assert cli.main() == 0
    assert called["config"].data_dir == tmp_path / "isolated"
    assert called["argv"] == ["retrieval-lab", "8123"]


def test_real_imports_share_cli_store_despite_conflicting_dotenv(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / ".env.local").write_text(
        f"YT_RAG_DATA_DIR={tmp_path / 'wrong-store'}\n"
    )
    code = f"""
from local_preview.config import configure_startup
selected = configure_startup({{'YT_RAG_WORKSPACE': {str(tmp_path)!r}, 'YT_RAG_ENV_DIR': {str(tmp_path)!r}, 'YT_RAG_DATA_DIR': {str(tmp_path / "selected-store")!r}}})
from local_preview import local_api
from pipelines import video_ocr_common, curate_evidence
assert local_api.APP_CONFIG == selected
assert video_ocr_common.DEFAULT_DATA_DIR == selected.data_dir
assert curate_evidence.DEFAULT_DATA_DIR == selected.data_dir
assert local_api.SERVICE is None
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        check=True,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    assert not (tmp_path / "wrong-store").exists()
    assert not (tmp_path / "selected-store").exists()


def test_checkout_legacy_history_is_loaded_without_mutating_original(
    tmp_path, monkeypatch
):
    from local_preview import local_api

    root = checkout(tmp_path)
    config = RuntimeConfig.resolve(source_root=root, environ={})
    config.legacy_data_dir.mkdir(parents=True)
    history = config.legacy_data_dir / "ask_history.json"
    original = json.dumps(
        {
            "SolarDemo01": [
                {
                    "question": "What is solar power?",
                    "answer": "Saved answer",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        }
    )
    history.write_text(original)
    monkeypatch.setattr(
        local_api,
        "RAGEngine",
        lambda **kwargs: SimpleNamespace(library=SimpleNamespace(videos={})),
    )
    service = local_api.LocalRAGService(config=config)
    assert service.ask_history["SolarDemo01"][0]["answer"] == "Saved answer"
    service._persist_ask_history()
    assert history.read_text() == original
    assert service.ask_history_path.exists()
    assert local_api.LocalRAGService(config=config).ask_history == service.ask_history


@pytest.mark.parametrize(
    "name", ["feedback_keys", "grounded_answer", "agentic_retrieval", "retrieval_tools"]
)
def test_legacy_import_is_same_module_not_a_fork(name):
    shared = importlib.import_module("retrieval_lab." + name)
    legacy = importlib.import_module("local_preview." + name)
    assert legacy is shared


@pytest.mark.parametrize("current", ["{broken", "[]", "{}"])
def test_history_recovery_preserves_damaged_primary_and_valid_current_wins(
    tmp_path, monkeypatch, current
):
    from local_preview import local_api

    config = RuntimeConfig.resolve(source_root=checkout(tmp_path), environ={})
    config.legacy_data_dir.mkdir(parents=True)
    legacy = config.legacy_data_dir / "ask_history.json"
    legacy_text = json.dumps({"SolarDemo01": [{"question": "Saved?", "answer": "Yes"}]})
    legacy.write_text(legacy_text)
    primary = config.data_dir / "runtime" / "ask_history.json"
    primary.parent.mkdir(parents=True)
    primary.write_text(current)
    monkeypatch.setattr(
        local_api,
        "RAGEngine",
        lambda **kwargs: SimpleNamespace(library=SimpleNamespace(videos={})),
    )
    service = local_api.LocalRAGService(config=config)
    assert primary.read_text() == current
    if current == "{}":
        assert service.ask_history == {}
    else:
        assert service.ask_history["SolarDemo01"][0]["answer"] == "Yes"
    service._persist_ask_history()
    backups = list(primary.parent.glob("ask_history.corrupt-*.json"))
    assert len(backups) == (0 if current == "{}" else 1)
    if backups:
        assert backups[0].read_text() == current
        assert backups[0].stat().st_mode & 0o777 == 0o600
    assert legacy.read_text() == legacy_text
    assert local_api.LocalRAGService(config=config).ask_history == service.ask_history


def test_helpers_import_without_models_or_application(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
from retrieval_lab import feedback_keys, grounded_answer, retrieval_tools, agentic_retrieval
assert not any(x in sys.modules for x in ('torch', 'sentence_transformers', 'local_preview.local_api'))
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        check=True,
    )


def test_bare_legacy_helper_import_from_unrelated_directory(tmp_path):
    local = Path(__file__).resolve().parents[1] / "local_preview"
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    code = f"import sys; sys.path.insert(0, {str(local)!r}); import feedback_keys; from retrieval_lab import feedback_keys as shared; assert feedback_keys is shared"
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, check=True)


def test_service_uses_one_isolated_store(tmp_path, monkeypatch):
    from local_preview import local_api

    received = {}

    def engine(**kwargs):
        received.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(local_api, "RAGEngine", engine)
    config = RuntimeConfig.resolve(
        environ={"YT_RAG_WORKSPACE": str(tmp_path), "YT_RAG_DATA_DIR": "store"}
    )
    service = local_api.LocalRAGService(config=config)
    assert received["data_dir"] == str(config.data_dir)
    assert service.runtime_data_dir == config.data_dir / "runtime"
    assert service.cache_data_dir == config.data_dir / "cache"
    assert service._local_ocr_data_dir() == config.data_dir
    assert service.legacy_data_dir == config.data_dir / "legacy_local_preview"
    assert service._eval_generator_service().root_dir == tmp_path
    schema = service._eval_generator_service().schema_path
    assert schema.is_file()
    assert not schema.is_relative_to(tmp_path)
    assert json.loads(schema.read_text())["type"] == "object"


def test_importing_api_does_not_initialize_library(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = """
from unittest.mock import patch
with patch('multilingual.rag_engine.RAGEngine', side_effect=AssertionError('unexpected model initialization')):
    from local_preview import local_api
    assert local_api.SERVICE is None
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        check=True,
        env={
            **os.environ,
            "PYTHONPATH": str(root),
            "YT_RAG_WORKSPACE": str(tmp_path),
            "YT_RAG_DATA_DIR": str(tmp_path / "store"),
            "YT_RAG_SKIP_GLOBAL_SERVICE": "0",
        },
    )
    assert not (tmp_path / "store").exists()
