from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.operator.reporting import render_operator_report_text
from quantpilot.packages.core.operator.schemas import OperatorRunRequest
from quantpilot.packages.core.operator.service import OperatorService
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import BrokerMode, ExecutionMode, PortfolioSnapshot, UserPolicy, utc_now
from quantpilot.packages.core.strategies.promotion import load_lifecycle_fixture
from quantpilot.packages.core.strategies.registry import StrategyRegistry, StrategyRegistryEntry
from quantpilot.services.api.dependencies import get_operator_service
from quantpilot.services.api.main import app


FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"


def _promoted_policy(**updates: object) -> UserPolicy:
    values: dict[str, object] = {
        "version": 5,
        "execution_mode": ExecutionMode.fully_automated,
        "broker": BrokerMode.mock,
        "authority_level": 5,
        "fully_automated_operator_enabled": True,
    }
    values.update(updates)
    return UserPolicy(**values)


def _level5_registry() -> StrategyRegistry:
    return StrategyRegistry(
        [
            StrategyRegistryEntry(
                strategy_id="pullback_trend_v1",
                version="1.0.0",
                spec_hash="sha256:fixture-pullback-trend-v1-validated-snapshot",
                status="validated_l5",
                allowed_execution_levels=["level_5", "fully_automated"],
                priority=10,
            ),
        ],
        lifecycle_records=load_lifecycle_fixture(),
    )


def _service_with_policy(policy: UserPolicy) -> OperatorService:
    service = OperatorService(HarnessService(), registry=_level5_registry())
    service.repositories.policies.add(policy)
    return service


def _request(policy: UserPolicy, *, run_mode: str = "mock_submit", key: str = "level5-run") -> OperatorRunRequest:
    return OperatorRunRequest(
        policy_id=policy.policy_id,
        requested_policy_version=policy.version,
        run_mode=run_mode,
        idempotency_key=key,
    )


@pytest.fixture
def operator_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FULLY_AUTOMATED_OPERATOR_ENABLED", "true")
    monkeypatch.setattr(
        "quantpilot.packages.core.execution.state_machine.is_krx_auto_order_window",
        lambda now=None: True,
    )


def test_level5_run_once_blocks_when_feature_flag_is_disabled() -> None:
    service = OperatorService()
    request = OperatorRunRequest(
        policy_id="pol_level5_fixture",
        requested_policy_version=5,
        run_mode="dry_run",
        idempotency_key="level5-disabled-fixture",
    )

    result = service.run_once(request)

    assert result.status == "blocked"
    assert result.submitted_order_plan_ids == []
    assert result.fallback.reason_code == "level5_flag_disabled"
    assert result.report.live_trading_enabled is False


def test_level5_run_once_completes_with_mock_broker(operator_enabled: None) -> None:
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))

    assert result.status == "completed"
    assert result.submitted_order_plan_ids
    assert result.report.broker_order_ids
    assert result.report.live_trading_enabled is False
    assert result.report.risk_check_ids
    assert result.report.strategy_selection.selected_strategy_id == "pullback_trend_v1"
    for order_plan_id in result.submitted_order_plan_ids:
        plan = service.repositories.order_plans.require(order_plan_id)
        assert plan.status.value == "filled"
        assert plan.risk_check_id is not None
    broker_orders = service.repositories.broker_orders.list()
    assert all(order.broker_mode == BrokerMode.mock for order in broker_orders)
    actions = {event.action for event in service.repositories.audit_logs.list()}
    assert "operator_order_submitted" in actions
    assert "operator_report_generated" in actions


def test_level5_run_once_completes_with_paper_broker(operator_enabled: None) -> None:
    policy = _promoted_policy(broker=BrokerMode.paper)
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy, run_mode="paper_submit"))

    assert result.status == "completed"
    assert result.submitted_order_plan_ids
    broker_orders = service.repositories.broker_orders.list()
    assert broker_orders
    assert all(order.broker_mode == BrokerMode.paper for order in broker_orders)


def test_level5_dry_run_creates_proposals_but_submits_nothing(operator_enabled: None) -> None:
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy, run_mode="dry_run"))

    assert result.status == "completed"
    assert result.submitted_order_plan_ids == []
    assert result.report.order_plan_ids
    assert service.repositories.broker_orders.list() == []
    for order_plan_id in result.report.order_plan_ids:
        assert service.repositories.order_plans.require(order_plan_id).status.value == "proposed"


def test_level5_policy_flag_alone_enables_run_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FULLY_AUTOMATED_OPERATOR_ENABLED", raising=False)
    monkeypatch.setattr(
        "quantpilot.packages.core.execution.state_machine.is_krx_auto_order_window",
        lambda now=None: True,
    )
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))

    assert result.status == "completed"
    assert result.submitted_order_plan_ids


def test_level5_policy_kill_switch_blocks_run(operator_enabled: None) -> None:
    policy = _promoted_policy(kill_switch_engaged=True)
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))

    assert result.status == "blocked"
    assert result.fallback.reason_code == "kill_switch_engaged"
    assert result.submitted_order_plan_ids == []


def test_level5_operator_env_kill_switch_blocks_run(operator_enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_KILL_SWITCH", "true")
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))

    assert result.status == "blocked"
    assert result.fallback.reason_code == "operator_kill_switch_engaged"
    assert result.submitted_order_plan_ids == []


def test_level5_refuses_to_run_if_live_trading_env_is_enabled(operator_enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))

    assert result.status == "blocked"
    assert result.fallback.reason_code == "live_trading_flag_engaged"
    assert result.submitted_order_plan_ids == []
    assert service.repositories.broker_orders.list() == []


def test_level5_policy_version_mismatch_blocks_and_requires_review(operator_enabled: None) -> None:
    policy = _promoted_policy()
    service = _service_with_policy(policy)
    request = OperatorRunRequest(
        policy_id=policy.policy_id,
        requested_policy_version=policy.version - 1,
        run_mode="mock_submit",
        idempotency_key="level5-version-drift",
    )

    result = service.run_once(request)

    assert result.status == "blocked"
    assert result.fallback.reason_code == "policy_review_required"
    actions = {event.action for event in service.repositories.audit_logs.list()}
    assert "policy_version_mismatch" in actions


def test_level5_unpromoted_policy_falls_back_to_level4(operator_enabled: None) -> None:
    policy = _promoted_policy(authority_level=4, execution_mode=ExecutionMode.guarded_autopilot)
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))

    assert result.status == "fallback"
    assert result.fallback.reason_code == "policy_not_promoted"
    assert result.fallback.to_level == 4
    assert result.submitted_order_plan_ids == []


def test_level5_default_registry_has_no_eligible_strategy(operator_enabled: None) -> None:
    policy = _promoted_policy()
    service = OperatorService(HarnessService())
    service.repositories.policies.add(policy)

    result = service.run_once(_request(policy))

    assert result.status == "fallback"
    assert result.fallback.reason_code == "no_level5_strategy_eligible"
    assert result.fallback.to_level == 4
    assert result.submitted_order_plan_ids == []
    assert result.report.strategy_selection.selected_strategy_id is None


def test_level5_registry_without_lifecycle_evidence_blocks_submission(operator_enabled: None) -> None:
    policy = _promoted_policy()
    registry = StrategyRegistry(
        [
            StrategyRegistryEntry(
                strategy_id="pullback_trend_v1",
                version="1.0.0",
                spec_hash="sha256:fixture-pullback-trend-v1-validated-snapshot",
                status="validated_l5",
                allowed_execution_levels=["level_5", "fully_automated"],
                priority=10,
            )
        ]
    )
    service = OperatorService(HarnessService(), registry=registry)
    service.repositories.policies.add(policy)

    result = service.run_once(_request(policy))

    assert result.status == "fallback"
    assert result.fallback.reason_code == "no_approved_strategy_available"
    assert result.submitted_order_plan_ids == []
    assert result.report.strategy_selection.rejected["pullback_trend_v1"] == "lifecycle_record_missing"
    assert service.repositories.broker_orders.list() == []


def test_level5_monthly_loss_stop_blocks_all_automatic_trading(operator_enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        MockBroker,
        "get_positions",
        lambda self, user_id: fixture_portfolio_snapshot(monthly_loss_ratio=-0.12),
    )
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))

    assert result.status == "blocked"
    assert result.fallback.reason_code == "monthly_loss_stop_engaged"
    assert result.submitted_order_plan_ids == []
    assert service.repositories.broker_orders.list() == []


def test_level5_monthly_loss_pause_blocks_new_buys(operator_enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        MockBroker,
        "get_positions",
        lambda self, user_id: fixture_portfolio_snapshot(monthly_loss_ratio=-0.06),
    )
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))

    for order_plan_id in result.submitted_order_plan_ids:
        plan = service.repositories.order_plans.require(order_plan_id)
        assert plan.intent.side == "sell", "monthly loss pause must block new automatic buys"


def test_level5_stale_market_data_blocks_submission(operator_enabled: None) -> None:
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy), now=utc_now() + timedelta(hours=1))

    assert result.status == "fallback"
    assert result.fallback.reason_code == "stale_market_data"
    assert result.fallback.to_level == 3
    assert result.submitted_order_plan_ids == []
    assert service.repositories.broker_orders.list() == []


def test_level5_broker_failure_pauses_execution_and_reports(operator_enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken_submit(self, order_plan):
        raise RuntimeError("broker connection lost")

    monkeypatch.setattr(MockBroker, "submit_order", _broken_submit)
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))

    assert result.status == "fallback"
    assert result.fallback.reason_code == "broker_failure"
    assert result.submitted_order_plan_ids == []
    assert service.repositories.broker_orders.list() == []
    assert service.harness.autopilot_paused is True
    actions = {event.action for event in service.repositories.audit_logs.list()}
    assert "broker_health_failed" in actions
    assert "operator_report_generated" in actions


def test_level5_duplicate_run_key_does_not_duplicate_orders(operator_enabled: None) -> None:
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    first = service.run_once(_request(policy, key="level5-idempotent"))
    orders_after_first = len(service.repositories.order_plans.list())
    second = service.run_once(_request(policy, key="level5-idempotent"))

    assert first.run_id == second.run_id
    assert second.submitted_order_plan_ids == first.submitted_order_plan_ids
    assert len(service.repositories.order_plans.list()) == orders_after_first
    actions = [event.action for event in service.repositories.audit_logs.list()]
    assert "operator_duplicate_run_ignored" in actions


def test_level5_report_renders_deterministically_without_llm(operator_enabled: None) -> None:
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))
    text = render_operator_report_text(result.report)

    assert result.run_id in text
    assert "Live trading enabled: NO" in text
    assert "Safety flags:" in text
    assert "Risk checks:" in text
    assert f"Policy {policy.policy_id} version {policy.version}" in text
    assert text == render_operator_report_text(result.report), "rendering must be deterministic"


def test_level5_empty_registry_falls_back_to_level2_suggestions(operator_enabled: None) -> None:
    policy = _promoted_policy()
    service = OperatorService(HarnessService(), registry=StrategyRegistry([]))
    service.repositories.policies.add(policy)

    result = service.run_once(_request(policy))

    assert result.status == "fallback"
    assert result.fallback.reason_code == "no_approved_strategy_available"
    assert result.fallback.to_level == 2
    assert result.submitted_order_plan_ids == []


def test_level5_missing_policy_blocks_run(operator_enabled: None) -> None:
    service = OperatorService(HarnessService(), registry=_level5_registry())
    request = OperatorRunRequest(
        policy_id="pol_does_not_exist",
        requested_policy_version=1,
        run_mode="mock_submit",
        idempotency_key="level5-missing-policy",
    )

    result = service.run_once(request)

    assert result.status == "blocked"
    assert result.fallback.reason_code == "policy_not_found"
    assert result.submitted_order_plan_ids == []


def test_level5_run_mode_broker_mismatch_is_blocked(operator_enabled: None) -> None:
    policy = _promoted_policy(broker=BrokerMode.mock)
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy, run_mode="paper_submit"))

    assert result.status == "blocked"
    assert result.fallback.reason_code == "run_mode_broker_mismatch"
    assert result.submitted_order_plan_ids == []
    assert service.repositories.broker_orders.list() == []


def test_level5_expired_risk_check_blocks_submission(operator_enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # Shift only the submission-time clock forward so the proposal-stage risk check
    # expires before submit_order_plan re-validates it.
    monkeypatch.setattr(
        "quantpilot.packages.core.harness_service.utc_now",
        lambda: utc_now() + timedelta(minutes=20),
    )
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    result = service.run_once(_request(policy))

    assert result.submitted_order_plan_ids == []
    assert result.status == "fallback"
    assert result.fallback.reason_code == "risk_check_failed"
    assert result.fallback.to_level == 2
    assert service.repositories.broker_orders.list() == []


def test_level5_kill_switch_engaged_after_run_is_not_masked_by_duplicate_key(operator_enabled: None) -> None:
    policy = _promoted_policy()
    service = _service_with_policy(policy)

    first = service.run_once(_request(policy, key="level5-replay-guard"))
    assert first.status == "completed"

    policy.kill_switch_engaged = True
    service.repositories.policies.update(policy)
    second = service.run_once(_request(policy, key="level5-replay-guard"))

    assert second.run_id != first.run_id
    assert second.status == "blocked"
    assert second.fallback.reason_code == "kill_switch_engaged"
    assert second.submitted_order_plan_ids == []


def test_level5_fixture_policy_and_registry_fall_back_to_level4(operator_enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = UserPolicy(**json.loads((FIXTURES_DIR / "operator_policy.json").read_text(encoding="utf-8")))
    snapshot = PortfolioSnapshot(**json.loads((FIXTURES_DIR / "operator_portfolio_snapshot.json").read_text(encoding="utf-8")))
    entries = [
        StrategyRegistryEntry(**raw)
        for raw in json.loads((FIXTURES_DIR / "operator_strategy_registry.json").read_text(encoding="utf-8"))
    ]
    monkeypatch.setattr(MockBroker, "get_positions", lambda self, user_id: snapshot)
    service = OperatorService(HarnessService(), registry=StrategyRegistry(entries))
    service.repositories.policies.add(policy)

    result = service.run_once(
        OperatorRunRequest(
            policy_id=policy.policy_id,
            requested_policy_version=policy.version,
            run_mode="mock_submit",
            idempotency_key="level5-fixture-policy",
        )
    )

    # The fixture policy is authority level 4 / guarded mode: Level 5 must fall back.
    assert result.status == "fallback"
    assert result.fallback.reason_code == "policy_not_promoted"
    assert result.fallback.to_level == 4
    assert result.submitted_order_plan_ids == []


def test_operator_api_run_once_is_blocked_by_default() -> None:
    service = OperatorService(HarnessService())
    app.dependency_overrides[get_operator_service] = lambda: service
    try:
        response = TestClient(app).post(
            "/api/operator/run-once",
            json={
                "policy_id": "pol_level5_fixture",
                "requested_policy_version": 5,
                "run_mode": "dry_run",
                "idempotency_key": "api-default-blocked",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["fallback"]["reason_code"] == "level5_flag_disabled"
    assert body["report"]["live_trading_enabled"] is False
    assert body["submitted_order_plan_ids"] == []
