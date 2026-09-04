from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from quantpilot.services.research_agents.runner import AgentEmptyOutput, AgentRunError, run_agent


class FakeRun:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "", raise_timeout: bool = False) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raise_timeout = raise_timeout
        self.captured: dict[str, Any] = {}

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.captured = {"command": command, **kwargs}
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 0))
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


def _payload(result: str = "", **extra: Any) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result, **extra})


def test_runner_passes_prompt_on_stdin_with_utf8_and_agent_flag() -> None:
    fake = FakeRun(stdout=_payload("코스피는 상승했다."))
    result = run_agent("qp-market-editor", "프롬프트 본문", cwd=".", model="opus", claude_path="claude.exe", run=fake)

    assert result.text == "코스피는 상승했다."
    assert result.json_output is None
    assert fake.captured["input"] == "프롬프트 본문"
    assert fake.captured["encoding"] == "utf-8"
    assert fake.captured["env"]["PYTHONIOENCODING"] == "utf-8"
    cmd = fake.captured["command"]
    assert cmd[:2] == ["claude.exe", "-p"] and "--agent" in cmd and cmd[cmd.index("--agent") + 1] == "qp-market-editor"
    assert cmd[cmd.index("--model") + 1] == "opus" and "--output-format" in cmd
    assert "--strict-mcp-config" in cmd  # RA-001: no inherited user-level MCP servers


def test_runner_treats_exit_zero_with_empty_output_as_failure() -> None:
    with pytest.raises(AgentEmptyOutput):
        run_agent("qp-x", "p", cwd=".", model="opus", claude_path="c", run=FakeRun(stdout=""))
    with pytest.raises(AgentEmptyOutput):
        run_agent("qp-x", "p", cwd=".", model="opus", claude_path="c", run=FakeRun(stdout=_payload("   ")))


def test_runner_surfaces_nonzero_exit_and_is_error() -> None:
    with pytest.raises(AgentRunError, match="exit 1"):
        run_agent("qp-x", "p", cwd=".", model="opus", claude_path="c", run=FakeRun(returncode=1, stderr="boom"))
    with pytest.raises(AgentRunError, match="is_error"):
        run_agent("qp-x", "p", cwd=".", model="opus", claude_path="c", run=FakeRun(stdout=_payload("limit", is_error=True)))


def test_runner_timeout_is_an_error() -> None:
    with pytest.raises(AgentRunError, match="timed out"):
        run_agent("qp-x", "p", cwd=".", model="opus", claude_path="c", run=FakeRun(raise_timeout=True), timeout_s=5)


def test_runner_returns_structured_output_when_schema_given() -> None:
    schema = {"type": "object", "properties": {"slack_text": {"type": "string"}}}
    structured = {"slack_text": "지수 한 줄", "note_markdown": "# 노트"}
    fake = FakeRun(stdout=_payload("", structured_output=structured))
    result = run_agent("qp-market-editor", "p", cwd=".", model="opus", claude_path="c", json_schema=schema, run=fake)

    assert result.json_output == structured
    assert "--json-schema" in fake.captured["command"]
    with pytest.raises(AgentEmptyOutput, match="structured"):
        run_agent("qp-x", "p", cwd=".", model="opus", claude_path="c", json_schema=schema, run=FakeRun(stdout=_payload("plain text")))
