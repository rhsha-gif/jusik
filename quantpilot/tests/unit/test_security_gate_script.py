from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "security-gate.ps1"
_AGENT = _ROOT / ".claude" / "agents" / "qp-security-gate.md"
_TRIGGERS = (
    "quantpilot/packages/core/execution/**",
    "quantpilot/packages/brokers/**",
    "quantpilot/packages/core/risk/**",
    "quantpilot/packages/core/operator/**",
    "quantpilot/services/api/**",
    "quantpilot/services/research_agents/**",
    "quantpilot/jobs/**",
    ".env*",
    ".mcp.json",
    ".claude/**",
    "tach.toml",
    "pyproject.toml",
)


def test_gate_script_runs_all_three_tools_and_fails_closed() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    for tool in ("gitleaks", "semgrep", "tach"):
        assert f"$tools.{tool}" in text, tool
    assert "--redact" in text, "gitleaks must never write secret values into the report"
    assert "tool-missing" in text and "exit 1" in text
    assert "summary.json" in text and "diff.patch" in text
    assert "Get-Content" not in text.replace("Get-Content -LiteralPath $gitleaksReport", "").replace("Get-Content -LiteralPath $semgrepReport", "")


def test_gate_agent_declares_every_ship_trigger_and_judges_only() -> None:
    text = _AGENT.read_text(encoding="utf-8")
    head = text.split("---")[1]
    assert "ship_triggers:" in head
    for trigger in _TRIGGERS:
        assert f'"{trigger}"' in head, trigger
    assert "disallowedTools:" in head and "Write" in head and "Edit" in head
    assert "\ntools:" not in head
    assert "roadmap_acceptance_matrix.md" in text
    assert "verdict" in text and "block" in text
