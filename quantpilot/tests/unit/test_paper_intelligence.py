from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_cli_metadata_overrides_model_self_description(monkeypatch, provider):
    content = {"model": "invented_by_model"}
    stdout = (
        json.dumps(
            {"structured_output": content, "modelUsage": {"verified_cli_model": {}}}
        )
        if provider == "claude"
        else json.dumps(content)
    )
    monkeypatch.setattr(
        "quantpilot.paper.intelligence.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=stdout, stderr="model: verified_cli_model\n"
        ),
    )
    assert (
        default_cli_runner(provider, "fixture", {"type": "object"})["model"]
        == "verified_cli_model"
    )


from quantpilot.paper.intelligence import (
    Assessment,
    IntelligenceError,
    IntelligenceErrorCode,
    MAX_VALIDITY,
    default_cli_runner,
    run_assessment,
    run_review,
)

NOW = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)
EVIDENCE = {
    "symbols": ["005930", "000660"],
    "strategies": ["opening_range", "pullback"],
    "facts": {"market_regime": "quiet"},
}


def assessment_payload(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "model": "subscription-model",
        "candidate_scores": {"005930": 0.7},
        "strategy_scores": {"pullback": 0.6},
        "reasons": {"005930": "liquid candidate"},
    }
    value.update(updates)
    return value


def test_assessment_is_strict_bounded_and_has_explicit_usability() -> None:
    item = Assessment(
        provider="claude",
        model="model",
        observed_at=NOW,
        expires_at=NOW + MAX_VALIDITY,
        candidate_scores={"005930": 1.0},
        strategy_scores={"pullback": 0.0},
        reasons={"005930": "reason"},
    )

    assert item.usable(NOW)
    assert item.usable(NOW + MAX_VALIDITY - timedelta(microseconds=1))
    assert not item.usable(NOW - timedelta(microseconds=1))
    assert not item.usable(NOW + MAX_VALIDITY)

    with pytest.raises(ValidationError):
        Assessment(
            provider="claude",
            model="model",
            observed_at=NOW,
            expires_at=NOW + MAX_VALIDITY + timedelta(seconds=1),
            candidate_scores={},
            strategy_scores={},
            reasons={},
        )
    with pytest.raises(ValidationError):
        Assessment(
            provider="codex",
            model="model",
            observed_at=NOW.replace(tzinfo=None),
            expires_at=NOW + timedelta(minutes=1),
            candidate_scores={},
            strategy_scores={},
            reasons={},
        )
    with pytest.raises(ValidationError):
        Assessment(
            provider="codex",
            model="model",
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
            candidate_scores={"x": math.nan},
            strategy_scores={},
            reasons={},
        )


def test_assessment_forbids_order_like_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Assessment(
            provider="codex",
            model="model",
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
            candidate_scores={},
            strategy_scores={},
            reasons={},
            order_quantity=10,
        )


def test_run_assessment_fails_over_exactly_once_and_host_stamps_authority() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def runner(
        provider: str, prompt: str, schema: dict[str, object]
    ) -> dict[str, object]:
        calls.append((provider, schema))
        if provider == "claude":
            return {"not": "valid"}
        return assessment_payload()

    result = run_assessment(EVIDENCE, NOW, primary="claude", runner=runner)

    assert isinstance(result, Assessment)
    assert [provider for provider, _ in calls] == ["claude", "codex"]
    assert result.provider == "codex"
    assert result.observed_at == NOW
    assert result.expires_at == NOW + MAX_VALIDITY
    assert "provider" not in calls[-1][1]["properties"]
    assert "expires_at" not in calls[-1][1]["properties"]


def test_out_of_allowlist_and_malformed_outputs_fail_closed() -> None:
    calls: list[str] = []

    def runner(
        provider: str, _prompt: str, _schema: dict[str, object]
    ) -> dict[str, object]:
        calls.append(provider)
        if provider == "claude":
            return assessment_payload(candidate_scores={"NOT_ALLOWED": 0.5})
        return assessment_payload(order={"symbol": "005930"})

    result = run_assessment(EVIDENCE, NOW, runner=runner)

    assert isinstance(result, IntelligenceError)
    assert result.code is IntelligenceErrorCode.MALFORMED_OUTPUT
    assert calls == ["claude", "codex"]
    assert result.attempted_providers == ("claude", "codex")


def test_run_assessment_rejects_nonfinite_evidence_before_runner() -> None:
    called = False

    def runner(*_args: object) -> dict[str, object]:
        nonlocal called
        called = True
        return assessment_payload()

    result = run_assessment({**EVIDENCE, "bad": math.inf}, NOW, runner=runner)

    assert isinstance(result, IntelligenceError)
    assert result.code is IntelligenceErrorCode.INVALID_INPUT
    assert not called


def test_review_is_prose_only_and_numeric_claims_fail_both_attempts() -> None:
    calls: list[str] = []

    def runner(
        provider: str, _prompt: str, _schema: dict[str, object]
    ) -> dict[str, object]:
        calls.append(provider)
        return {
            "model": "review-model",
            "summary": "PnL rose by 10 percent",
            "observations": [],
            "risks": [],
        }

    result = run_review(EVIDENCE, NOW, runner=runner)

    assert isinstance(result, IntelligenceError)
    assert result.code is IntelligenceErrorCode.PROSE_NUMERIC_CLAIM
    assert calls == ["claude", "codex"]


def test_review_succeeds_after_failover_without_numeric_report_data() -> None:
    def runner(
        provider: str, _prompt: str, _schema: dict[str, object]
    ) -> dict[str, object]:
        if provider == "codex":
            raise TimeoutError
        return {
            "model": "review-model",
            "summary": "Market evidence is internally consistent",
            "observations": ["Liquidity evidence supports continued observation"],
            "risks": ["Regime stability remains uncertain"],
        }

    result = run_review(EVIDENCE, NOW, primary="codex", runner=runner)

    assert not isinstance(result, IntelligenceError)
    assert result.provider == "claude"
    assert result.usable(NOW)


def test_default_codex_cli_has_no_shell_tools_and_sanitizes_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("BROKER_API_KEY", "secret")
    monkeypatch.setenv("SLACK_TOKEN", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="raw secret")

    monkeypatch.setattr("quantpilot.paper.intelligence.subprocess.run", fake_run)

    assert default_cli_runner("codex", "prompt", {"type": "object"}) == {"ok": True}
    command = captured["command"]
    assert isinstance(command, list)
    disabled = {
        command[i + 1] for i, value in enumerate(command[:-1]) if value == "--disable"
    }
    assert {
        "shell_tool",
        "unified_exec",
        "multi_agent",
        "view_image",
        "image_generation",
    } <= disabled
    assert "--skip-git-repo-check" in command
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["encoding"] == "utf-8"
    environment = captured["kwargs"]["env"]
    assert "BROKER_API_KEY" not in environment
    assert "SLACK_TOKEN" not in environment
    assert "OPENAI_API_KEY" not in environment


def test_default_claude_cli_disables_tools_mcp_and_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout='{"structured_output":{"ok":true}}',
            stderr="raw secret",
        )

    monkeypatch.setattr("quantpilot.paper.intelligence.subprocess.run", fake_run)

    assert default_cli_runner("claude", "prompt", {"type": "object"}) == {"ok": True}
    command = captured["command"]
    assert isinstance(command, list)
    assert "--safe-mode" in command
    assert "--restricted" in command
    tools_index = command.index("--tools")
    assert command[tools_index + 1] == ""
    assert "--strict-mcp-config" in command
    assert json.loads(command[command.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert "--no-session-persistence" in command
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["encoding"] == "utf-8"


def test_cli_unicode_streams_are_decoded_as_utf8(monkeypatch):
    import subprocess
    import sys

    original_run = subprocess.run

    def local_process(_command, **kwargs):
        # Reproduce UTF-8 CLI streams without any external provider or credentials.
        code = (
            "import sys; "
            "sys.stdout.buffer.write('{\"summary\":\"검증 ✓\"}'.encode('utf-8')); "
            "sys.stderr.buffer.write('진단 —'.encode('utf-8'))"
        )
        return original_run([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr("quantpilot.paper.intelligence.subprocess.run", local_process)
    assert default_cli_runner("codex", "fixture", {"type": "object"}) == {
        "summary": "검증 ✓"
    }


def test_assessment_wire_schema_closes_every_map_over_current_allowlists():
    captured = []

    def runner(provider, prompt, schema):
        captured.append(schema)
        return assessment_payload()

    assert isinstance(run_assessment(EVIDENCE, NOW, runner=runner), Assessment)
    props = captured[0]["properties"]
    for name, keys in (
        ("candidate_scores", EVIDENCE["symbols"]),
        ("strategy_scores", EVIDENCE["strategies"]),
        ("reasons", EVIDENCE["symbols"] + EVIDENCE["strategies"]),
    ):
        assert props[name]["additionalProperties"] is False
        assert set(props[name]["properties"]) == set(keys)
        assert set(props[name]["required"]) == set(keys)
    # A subsequent request must not inherit another request's symbols.
    run_assessment({"symbols": [], "strategies": []}, NOW, runner=runner)
    assert captured[-1]["properties"]["candidate_scores"]["properties"] == {}
    assert captured[0]["properties"]["candidate_scores"]["properties"]
