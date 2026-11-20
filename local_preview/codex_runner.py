"""Shared Codex CLI invocation for local draft workflows."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Callable, List, Optional


CODEX_TIMEOUT_SECONDS = 300


def _safe_error_text(value: object, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


class CodexRunnerError(RuntimeError):
    """Expected CLI failure with an API-safe code and status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = int(status)


class CodexRunner:
    """Run schema-constrained Codex generation with a sanitized environment."""

    def __init__(
        self,
        command_runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
        path_getter: Optional[Callable[[], Optional[str]]] = None,
        *,
        include_diagnostics: bool = False,
    ):
        self.command_runner = command_runner or subprocess.run
        self._path_getter = path_getter or self._codex_path
        self._include_diagnostics = include_diagnostics
        self._lock = threading.Lock()
        self._capability_cache: Optional[dict] = None
        self._capability_cached_at = 0.0
        self._capability_path: Optional[str] = None

    @staticmethod
    def _codex_path() -> Optional[str]:
        override = str(os.environ.get("YT_RAG_CODEX_BIN") or "").strip()
        path = shutil.which(os.path.expanduser(override) if override else "codex")
        return os.path.abspath(path) if path else None

    def binary(self) -> Optional[str]:
        """Return the discovered executable path without running the CLI."""
        return self._path_getter()

    @staticmethod
    def _sanitized_env() -> dict:
        allowed = {
            "PATH",
            "HOME",
            "CODEX_HOME",
            "TMPDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        }
        return {key: value for key, value in os.environ.items() if key in allowed}

    def capabilities(self, *, force: bool = False) -> dict:
        codex_path = self.binary()
        with self._lock:
            if (
                not force
                and self._capability_cache is not None
                and self._capability_path == codex_path
                and time.monotonic() - self._capability_cached_at < 30
            ):
                return deepcopy(self._capability_cache)

        if not codex_path:
            result = {
                "available": False,
                "authenticated": False,
                "version": None,
                "model_override": str(os.environ.get("YT_RAG_CODEX_MODEL") or "")
                or None,
                "message": "Codex CLI was not found. Install Codex or set YT_RAG_CODEX_BIN.",
            }
        else:
            env = self._sanitized_env()
            try:
                version_run = self.command_runner(
                    [codex_path, "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                    env=env,
                )
                login_run = self.command_runner(
                    [codex_path, "login", "status"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                    env=env,
                )
                available = version_run.returncode == 0
                authenticated = available and login_run.returncode == 0
                version_text = _safe_error_text(
                    version_run.stdout or version_run.stderr, limit=120
                )
                result = {
                    "available": available,
                    "authenticated": authenticated,
                    "version": version_text or None,
                    "model_override": str(
                        os.environ.get("YT_RAG_CODEX_MODEL") or ""
                    ).strip()
                    or None,
                    "message": (
                        "Codex CLI is ready."
                        if authenticated
                        else "Codex CLI is installed but not authenticated. Run codex login."
                    ),
                }
            except (OSError, subprocess.SubprocessError) as exc:
                result = {
                    "available": False,
                    "authenticated": False,
                    "version": None,
                    "model_override": str(
                        os.environ.get("YT_RAG_CODEX_MODEL") or ""
                    ).strip()
                    or None,
                    "message": self._diagnostic(
                        "Codex CLI check failed.", exc, separator=": "
                    ),
                }

        with self._lock:
            self._capability_cache = deepcopy(result)
            self._capability_cached_at = time.monotonic()
            self._capability_path = codex_path
        return result

    def _codex_command(
        self, codex_path: str, temp_dir: str, schema_path: Optional[Path], model: Optional[str]
    ) -> List[str]:
        command = [
            codex_path,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--config",
            'approval_policy="never"',
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--strict-config",
            "--color",
            "never",
            "-C",
            str(temp_dir),
        ]
        if schema_path is not None:
            command.extend(["--output-schema", str(schema_path.resolve())])
        if model:
            command.extend(["--model", model])
        command.append("-")
        return command

    def _diagnostic(self, message: str, detail: object, separator: str = " ") -> str:
        if self._include_diagnostics:
            prefix = message.removesuffix(".") if separator == ": " else message
            return prefix + separator + _safe_error_text(detail)
        return message

    def run(
        self, prompt: str, schema_path: Path, *, on_generating=None
    ) -> tuple[object, Optional[str]]:
        """Return decoded output and the model override used for this invocation."""
        model = str(os.environ.get("YT_RAG_CODEX_MODEL") or "").strip() or None
        output, _ = self._run(prompt, schema_path, model, on_generating=on_generating)
        try:
            return json.loads(output), model
        except json.JSONDecodeError as exc:
            raise CodexRunnerError(
                "INVALID_CODEX_OUTPUT",
                "Codex returned output that was not valid JSON.",
                502,
            ) from exc

    def run_text(self, prompt: str, model: str, schema_path: Optional[Path] = None):
        """Run a text-only request and return the CLI version used."""
        return self._run(prompt, schema_path, model, text_only=True)

    def _run(self, prompt, schema_path, model, *, on_generating=None, text_only=False):
        capability = self.capabilities()
        codex_path = self.binary()
        if (
            not codex_path or not capability.get("authenticated")
            or (text_only and not capability.get("version"))
        ):
            raise CodexRunnerError(
                "CODEX_UNAVAILABLE", "Codex CLI is no longer available.", 503
            )

        if on_generating:
            on_generating()
        with tempfile.TemporaryDirectory(prefix="yt-rag-eval-generator-") as temp_dir:
            command = self._codex_command(codex_path, temp_dir, schema_path, model)
            if text_only:
                options = ["--ignore-rules", "--config", 'web_search="disabled"']
                for feature in (
                    "shell_tool", "unified_exec", "hooks", "apps", "plugins",
                    "multi_agent", "multi_agent_v2", "in_app_browser", "in_app_local_automation",
                ):
                    options.extend(["--config", f"features.{feature}=false"])
                command[-1:-1] = options
            try:
                completed = self.command_runner(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=CODEX_TIMEOUT_SECONDS,
                    env=self._sanitized_env(),
                )
            except subprocess.TimeoutExpired as exc:
                raise CodexRunnerError(
                    "CODEX_TIMEOUT",
                    "Codex generation exceeded the five-minute timeout.",
                    504,
                ) from exc
            except OSError as exc:
                raise CodexRunnerError(
                    "CODEX_EXEC_FAILED",
                    self._diagnostic(
                        "Codex could not be started.", exc, separator=": "
                    ),
                    502,
                ) from exc

        if completed.returncode != 0:
            raise CodexRunnerError(
                "CODEX_EXEC_FAILED",
                self._diagnostic(
                    "Codex generation failed.", completed.stderr or completed.stdout
                ),
                502,
            )
        return completed.stdout, capability["version"]
