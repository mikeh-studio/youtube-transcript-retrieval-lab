"""Exercise CLI flags, provenance and failure shapes with fake subprocesses."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_preview.cli_llm import ClaudeRunner, CliLLM
from local_preview.codex_runner import CodexRunner, CodexRunnerError
from tests.test_eval_generator_api import _request
from tests.test_local_api_summary_and_retrieval import _make_service, local_api


def test_codex_provider_uses_stdin_and_safe_flags(monkeypatch):
    calls = []
    monkeypatch.setenv("YT_RAG_CODEX_LLM_MODEL", "fixture-luna")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == "exec":
            return subprocess.CompletedProcess(command, 0, "Invented output.", "")
        return subprocess.CompletedProcess(command, 0, "codex-cli 1.2.3", "")

    cli = CliLLM(
        codex=CodexRunner(command_runner=run, path_getter=lambda: "/fake/codex")
    )
    monkeypatch.setattr(local_api, "CLI_LLM", cli)
    result = _make_service(enabled=False)._llm_text_response(
        provider="codex_cli",
        system_prompt="Summarize",
        user_message="Untrusted transcript.",
        max_tokens=100,
        temperature=0,
    )
    assert result == {
        "provider": "codex_cli",
        "model": "fixture-luna",
        "cli_version": "codex-cli 1.2.3",
        "text": "Invented output.",
    }
    command, options = calls[-1]
    assert command[:2] == ["/fake/codex", "exec"] and command[-1] == "-"
    for flag in (
        "--ephemeral",
        "--ignore-user-config",
        "--strict-config",
        "--skip-git-repo-check",
    ):
        assert flag in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "features.shell_tool=false" in command
    assert "Untrusted transcript." not in command
    assert (
        "Untrusted transcript." in options["input"]
        and "untrusted data" in options["input"]
    )
    assert options["timeout"] == 300
    assert all("KEY" not in key and "TOKEN" not in key for key in options["env"])


def test_summary_route_preserves_provider_timeout_without_retries(monkeypatch):
    calls = []

    def run(command, **options):
        if command[1] == "exec":
            calls.append(command)
            raise subprocess.TimeoutExpired(command, options["timeout"])
        return subprocess.CompletedProcess(command, 0, "codex-cli 1.2.3", "")

    cli = CliLLM(
        codex=CodexRunner(command_runner=run, path_getter=lambda: "/fake/codex")
    )
    service = _make_service(enabled=False)
    service.engine = SimpleNamespace(
        library=SimpleNamespace(
            videos={
                "invented": {
                    "full_transcript": {
                        "segments": [
                            {
                                "text": "The invented lamp casts a shadow.",
                                "start": 0,
                                "end": 10,
                            }
                        ]
                    }
                },
            }
        )
    )
    monkeypatch.setattr(local_api, "CLI_LLM", cli)
    monkeypatch.setattr(local_api, "SERVICE", service)
    status, _, payload = _request(
        "POST",
        "/v1/summaries/transcript",
        {
            "provider": "codex_cli",
            "video_id": "invented",
            "language": "en",
        },
    )
    assert status == 504 and json.loads(payload)["error"]["code"] == "CODEX_TIMEOUT"
    assert len(calls) == 1


def test_claude_safe_subscription_mode_and_schema(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "fixture-dir")
    monkeypatch.setenv("CODEX_HOME", "fixture-codex")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == "--help":
            flags = ClaudeRunner.REQUIRED_FLAGS | {
                "--json-schema",
                "--disable-slash-commands",
            }
            output = "\n".join(f"  {flag}  description" for flag in sorted(flags))
        elif command[1] == "--version":
            output = "1.2.3 (Claude Code)"
        else:
            output = json.dumps(
                {
                    "is_error": False,
                    "result": "plain",
                    "structured_output": {"a": "invented"},
                }
            )
        return subprocess.CompletedProcess(command, 0, output, "")

    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    cli = CliLLM(
        claude=ClaudeRunner(command_runner=run, path_getter=lambda: "/fake/claude")
    )
    prompts = {
        "provider": "claude_cli",
        "model": "opus",
        "system_prompt": "Test",
        "user_message": "Invented transcript",
    }
    result = cli.text_response(**prompts, schema_path=schema)
    assert result["cli_version"] == "1.2.3 (Claude Code)"
    assert result["model"] == "opus"
    assert json.loads(result["text"]) == {"a": "invented"}
    assert cli.text_response(**prompts)["text"] == "plain"
    assert len(calls) == 4
    command, options = calls[-2]
    assert command[:5] == ["/fake/claude", "-p", "--model", "opus", "--output-format"]
    assert {"--safe-mode", "--strict-mcp-config", "--no-session-persistence"} <= set(
        command
    )
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--setting-sources") + 1] == ""
    assert command[command.index("--json-schema") + 1] == schema.read_text()
    assert (
        json.loads(command[command.index("--settings") + 1])["disableAllHooks"] is True
    )
    assert "--bare" not in command
    assert "Invented transcript" not in command
    assert "Invented transcript" in options["input"]
    assert Path(options["cwd"]).name.startswith("yt-rag-claude-")
    assert (
        not {"ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR", "CODEX_HOME"}
        & options["env"].keys()
    )


@pytest.mark.parametrize(
    "failure,code,status",
    [
        ("old", "CLAUDE_UNAVAILABLE", 503),
        ("timeout", "CLAUDE_TIMEOUT", 504),
        ("json", "INVALID_CLAUDE_OUTPUT", 502),
        ("exit", "CLAUDE_EXEC_FAILED", 502),
    ],
)
def test_claude_rejects_unsafe_or_failed_requests(failure, code, status):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[1] == "--help":
            flags = ClaudeRunner.REQUIRED_FLAGS - (
                {"--safe-mode"} if failure == "old" else set()
            )
            output = "\n".join(f"  {flag}" for flag in flags)
        elif command[1] == "--version":
            output = "fixture-version"
        elif failure == "timeout":
            raise subprocess.TimeoutExpired(command, 300)
        else:
            output = "sensitive subprocess detail"
        return subprocess.CompletedProcess(
            command,
            int(failure == "exit" and command[1] == "-p"),
            output,
            "sensitive stderr",
        )

    claude = ClaudeRunner(command_runner=run, path_getter=lambda: "/fake/claude")
    with pytest.raises(CodexRunnerError) as exc:
        claude.run_text("Untrusted content.", "sonnet")
    assert (exc.value.code, exc.value.status) == (code, status)
    assert "sensitive" not in str(exc.value)
    assert (len(calls) == 2) == (failure == "old")


def test_claude_env_keeps_account_name_for_keychain_login(monkeypatch):
    monkeypatch.setenv("USER", "someone")
    monkeypatch.setenv("LOGNAME", "someone")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-pass")
    env = ClaudeRunner._env()
    assert env["USER"] == "someone" and env["LOGNAME"] == "someone"
    assert "ANTHROPIC_API_KEY" not in env
