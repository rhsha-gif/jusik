"""Headless Claude Code runner for the project agents in `.claude/agents/qp-*.md`.

Two measured traps from the SecondBrain weekly pipeline are handled here:
`claude.exe` can exit 0 having produced nothing (treated as a failure), and
redirected output on Windows arrives in the OEM code page unless UTF-8 is
forced on both sides of the pipe. The prompt goes through stdin because the
evidence JSON easily exceeds the Windows command-line limit.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from quantpilot.services.research_agents.models import AgentResult

DEFAULT_CLAUDE_PATH = Path.home() / ".local" / "bin" / "claude.exe"
# The job's own credentials (news search, Slack) are for the collectors and publishers;
# the agent process never needs them and must not be able to leak them (gate finding RA-101).
CREDENTIAL_PREFIXES = ("NCP_APIGW_", "NAVER_CLIENT_", "SLACK_", "QUANTPILOT_SLACK_")


def agent_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for name in list(env):
        if name.startswith(CREDENTIAL_PREFIXES):
            del env[name]
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class AgentRunError(RuntimeError):
    pass


class AgentEmptyOutput(AgentRunError):
    pass


def _extract(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    structured = payload.get("structured_output")
    text = payload.get("result") or ""
    if isinstance(structured, dict):
        return text or json.dumps(structured, ensure_ascii=False), structured
    if isinstance(text, str) and text.strip().startswith("{"):
        try:
            return text, json.loads(text)
        except json.JSONDecodeError:
            return text, None
    return text, None


def run_agent(
    agent: str,
    prompt: str,
    *,
    cwd: str | Path,
    model: str,
    timeout_s: int = 900,
    json_schema: dict[str, Any] | None = None,
    claude_path: str | Path | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> AgentResult:
    """Run one project agent headlessly and return its text (and JSON when a schema was given)."""

    executable = str(claude_path or DEFAULT_CLAUDE_PATH)
    command = [executable, "-p", "--agent", agent, "--model", model, "--output-format", "json"]
    # Only the project's own MCP servers (.mcp.json: vault) — never the user-level ones
    # (Slack, Figma, Supabase, ...) that a headless session would otherwise inherit (gate finding RA-001).
    project_mcp = Path(cwd) / ".mcp.json"
    command += ["--strict-mcp-config"]
    if project_mcp.exists():
        command += ["--mcp-config", str(project_mcp)]
    if json_schema is not None:
        command += ["--json-schema", json.dumps(json_schema, ensure_ascii=False)]
    env = agent_environment()
    started = time.monotonic()
    try:
        completed = run(
            command,
            input=prompt,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise AgentRunError(f"{agent}: timed out after {timeout_s}s") from exc
    except OSError as exc:
        raise AgentRunError(f"{agent}: could not start {executable}: {exc}") from exc
    elapsed = round(time.monotonic() - started, 1)

    if completed.returncode != 0:
        stderr = (completed.stderr or "")[:500]
        raise AgentRunError(f"{agent}: exit {completed.returncode}: {stderr}")

    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise AgentEmptyOutput(f"{agent}: exit 0 with empty stdout")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AgentRunError(f"{agent}: stdout is not JSON: {stdout[:200]}") from exc
    if payload.get("is_error"):
        raise AgentRunError(f"{agent}: claude reported is_error: {str(payload.get('result', ''))[:300]}")

    text, structured = _extract(payload)
    if not text.strip():
        raise AgentEmptyOutput(f"{agent}: exit 0 with empty result")
    if json_schema is not None and structured is None:
        raise AgentEmptyOutput(f"{agent}: schema requested but no structured output returned")
    return AgentResult(
        agent=agent,
        model=model,
        text=text,
        json_output=structured,
        elapsed_s=elapsed,
        exit_code=completed.returncode,
    )
