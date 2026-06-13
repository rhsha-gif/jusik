"""Narrow regression tests that protect the safety invariants listed in AGENTS.md.

Each test targets a specific code path that must remain fail-closed by default.
These tests do NOT call live brokers, read real credentials, or enable live trading.
"""
from __future__ import annotations

import pytest

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.risk.gatekeeper import allowed_execution_modes, run_risk_check
from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    OrderIntent,
    OrderPlan,
    OrderType,
    UserPolicy,
)
from quantpilot.packages.db.audit import REDACTED, AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


def _limit_order(policy: UserPolicy) -> OrderPlan:
    intent = OrderIntent(
        symbol="AAA",
        side="buy",
        order_type=OrderType.limit,
        quantity=5_000,
        limit_price=100,
        notional=500_000,
        target_weight=0.05,
        reason="safety invariant baseline test",
    )
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=intent,
        idempotency_key="idem-baseline-safety",
    )


# ── Broker guard ──────────────────────────────────────────────────────────────

def test_live_broker_mode_raises_in_broker_for_policy() -> None:
    """_broker_for_policy must refuse to return any broker for live_disabled mode."""
    service = HarnessService()
    policy = service.parse_policy()
    policy.broker = BrokerMode.live_disabled
    with pytest.raises(RuntimeError, match="live broker mode is disabled"):
        service._broker_for_policy(policy)


# ── Execution mode guard ──────────────────────────────────────────────────────

def test_allowed_execution_modes_excludes_fully_automated_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allowed_execution_modes must not include fully_automated when the env flag is absent."""
    monkeypatch.delenv("FULLY_AUTOMATED_OPERATOR_ENABLED", raising=False)
    modes = allowed_execution_modes(UserPolicy())
    assert ExecutionMode.fully_automated not in modes
    assert ExecutionMode.approval_required in modes
    assert ExecutionMode.paper_trading in modes
    assert ExecutionMode.guarded_autopilot in modes


def test_risk_check_execution_mode_allowed_blocks_fully_automated_without_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """risk check execution_mode_allowed must fail for fully_automated with default env flags."""
    monkeypatch.delenv("FULLY_AUTOMATED_OPERATOR_ENABLED", raising=False)
    policy = UserPolicy(
        execution_mode=ExecutionMode.fully_automated,
        authority_level=5,
        fully_automated_operator_enabled=False,
    )
    risk = run_risk_check(
        policy=policy,
        order_plan=_limit_order(policy),
        snapshot=fixture_portfolio_snapshot(),
    )
    assert not risk.passed
    assert "execution_mode_allowed" in risk.failed_checks


# ── Live trading hardcoded disabled ──────────────────────────────────────────

def test_autopilot_status_hardcodes_live_trading_disabled() -> None:
    """autopilot_status must return live_trading_enabled=False regardless of policy state."""
    service = HarnessService()
    policy = service.parse_policy()
    status = service.autopilot_status(policy_id=policy.policy_id)
    assert status["live_trading_enabled"] is False
    flags = status["feature_flags"]
    assert isinstance(flags, dict)
    assert flags["LIVE_TRADING_ENABLED"] is False
    assert flags["MARKET_ORDERS_ENABLED"] is False


def test_guarded_autopilot_run_once_hardcodes_live_trading_disabled() -> None:
    """run_guarded_autopilot_once must return live_trading_enabled=False in its result."""
    service = HarnessService()
    policy = service.parse_policy()
    result = service.run_guarded_autopilot_once(policy_id=policy.policy_id)
    assert result["live_trading_enabled"] is False


def test_audit_recorder_redacts_secret_shaped_fields() -> None:
    """Audit state must never persist provider or broker credentials verbatim."""
    repos = RepositoryRegistry()
    recorder = AuditRecorder(repos.audit_logs)

    event = recorder.emit(
        user_id="fixture-user",
        entity_type="policy",
        entity_id="pol_secret_test",
        action="policy_created",
        after_state={
            "KIS_APP_KEY": "fake-app-key",
            "authorization": "Bearer fake-token",
            "nested": {
                "access_token": "fake-access-token",
                "appsecret": "fake-app-secret",
                "idempotency_key": "not-a-secret-key",
            },
        },
        source="unit_test",
    )

    assert event.after_state is not None
    assert event.after_state["KIS_APP_KEY"] == REDACTED
    assert event.after_state["authorization"] == REDACTED
    assert event.after_state["nested"]["access_token"] == REDACTED
    assert event.after_state["nested"]["appsecret"] == REDACTED
    assert event.after_state["nested"]["idempotency_key"] == "not-a-secret-key"
