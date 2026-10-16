"""Resolve writable application paths independently of installed source files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class RuntimeConfig:
    """Locations shared by ingestion, indexes, caches, history, and OCR."""

    workspace_dir: Path
    data_dir: Path
    env_dir: Path
    static_dir: Path
    legacy_data_dir: Path

    @classmethod
    def resolve(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        source_root: Path | None = None,
    ) -> "RuntimeConfig":
        """Resolve without reading dotenv files, creating directories, or models."""
        env = os.environ if environ is None else environ
        root = (source_root or Path(__file__).resolve().parent.parent).resolve()
        is_checkout = (root / "pyproject.toml").is_file() and (
            root / "local_preview/local_api.py"
        ).is_file()
        user_root = (
            Path(env.get("XDG_DATA_HOME") or Path.home() / ".local/share")
            / "youtube-transcript-retrieval-lab"
        )
        workspace = (
            Path(env.get("YT_RAG_WORKSPACE") or (root if is_checkout else user_root))
            .expanduser()
            .resolve()
        )

        def location(key: str, default: Path) -> Path:
            value = Path(env.get(key) or default).expanduser()
            return (value if value.is_absolute() else workspace / value).resolve()

        data = location("YT_RAG_DATA_DIR", workspace / "data")
        # An explicitly isolated store must never import another store's history.
        legacy = (
            root / "local_preview/data"
            if is_checkout and workspace == root and data == root / "data"
            else data / "legacy_local_preview"
        )
        return cls(
            workspace,
            data,
            location("YT_RAG_ENV_DIR", workspace),
            location("YT_RAG_STATIC_DIR", root / "local_preview/web"),
            legacy,
        )


def load_runtime_config(*, overrides: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Load .env then .env.local at the selected location, matching legacy order."""
    overrides = dict(overrides or {})
    initial = RuntimeConfig.resolve(environ={**os.environ, **overrides})
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None
    if load_dotenv is not None:
        load_dotenv(initial.env_dir / ".env")
        load_dotenv(initial.env_dir / ".env.local", override=True)
    os.environ.update(overrides)
    return RuntimeConfig.resolve()


_startup_config: RuntimeConfig | None = None


def configure_startup(overrides: Mapping[str, str]) -> RuntimeConfig:
    """Select CLI paths once, before application/pipeline imports freeze defaults."""
    global _startup_config
    _startup_config = load_runtime_config(overrides=overrides)
    return _startup_config


def startup_config() -> RuntimeConfig:
    """Return the launcher configuration without loading dotenv a second time."""
    global _startup_config
    if _startup_config is None:
        _startup_config = load_runtime_config()
    return _startup_config
