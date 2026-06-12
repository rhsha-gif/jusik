from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.brokers.paper_broker import PaperBroker
from quantpilot.packages.core.analyst.fable5 import DirectOrderSubmissionBlocked, submit_order_from_fable5_recipe
from quantpilot.packages.core.execution.state_machine import ApprovalRequired, RiskCheckRequired, transition_order_plan
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.policy.parser import parse_policy_text
from quantpilot.packages.core.portfolio.planner import build_portfolio_plan, fixture_portfolio_snapshot
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
from quantpilot.packages.core.schemas import (
    BrokerMode,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    OrderType,
    SignalAction,
    UserPolicy,
    utc_now,
)
from quantpilot.packages.core.signals.service import generate_signals, load_fixture_ohlcv
from quantpilot.packages.core.strategies.loader import load_default_strategy
from quantpilot.packages.db.audit import AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


def sample_order_plan(
    policy: UserPolicy | None = None,
    *,
    side: str = "buy",
    order_type: OrderType = OrderType.limit,
    notional: float = 500_000,
    symbol: str = "AAA",
    idempotency_key: str = "idem-1",
) -> OrderPlan:
    active_policy = policy or parse_policy_text("fixture")
    price = 100.0
    intent = OrderIntent(
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=notional / price,
        limit_price=price if order_type == OrderType.limit else None,
        notional=notional,
        target_weight=notional / 10_000_000,
        reason="test order",
    )
    return OrderPlan(
        policy_id=active_policy.policy_id,
        policy_version=active_policy.version,
        intent=intent,
        idempotency_key=idempotency_key,
    )


def test_invalid_policy_weights_are_rejected() -> None:
    with pytest.raises(ValidationError):
        UserPolicy(max_position_weight=0.90, min_cash_weight=0.20)


def test_invalid_loss_limits_are_rejected() -> None:
    with pytest.raises(ValidationError):
        UserPolicy(daily_loss_limit=0.03)


def test_strategy_recipe_loads() -> None:
    recipe = load_default_strategy()
    assert recipe.strategy_id == "pullback_trend_v1"
    assert recipe.status.value == "draft"


def test_all_signal_actions_can_be_produced_from_fixtures() -> None:
    signals = generate_signals(load_default_strategy(), load_fixture_ohlcv())
    assert {signal.action for signal in signals} == set(SignalAction)


def test_portfolio_planner_respects_max_position_min_cash_and_order_limit() -> None:
    policy = parse_policy_text("fixture")
    signals = generate_signals(load_default_strategy(), load_fixture_ohlcv())
    quotes = {bar["symbol"]: float(bar["close"]) for bar in load_fixture_ohlcv()}
    plan = build_portfolio_plan(policy=policy, signals=signals, snapshot=fixture_portfolio_snapshot(), quotes=quotes)

    by_symbol = {signal.symbol: signal.action for signal in signals}
    assert plan.target_weights["GGG"] == 0
    assert plan.target_weights["EEE"] == 0
    assert plan.target_weights["BBB"] == 0
    assert plan.target_weights["AAA"] <= policy.max_position_weight
    assert plan.cash_target_weight >= policy.min_cash_weight
    assert all(intent.notional <= policy.single_order_cash_limit for intent in plan.order_intents)
    assert by_symbol["AAA"] == SignalAction.buy_ready


def test_risk_check_is_required_before_submit() -> None:
    service = HarnessService()
    policy = service.parse_policy()
    signals = service.run_signals()
    plan = service.create_portfolio_plan(policy_id=policy.policy_id, signals=signals)
    orders = service.create_order_plans(portfolio_plan_id=plan.plan_id, run_risk=False)

    with pytest.raises(RiskCheckRequired):
        service.submit_order_plan(orders[0].order_plan_id)


def test_approval_is_required_before_submit_in_approval_required_mode() -> None:
    service = HarnessService()
    policy = service.parse_policy()
    signals = service.run_signals()
    plan = service.create_portfolio_plan(policy_id=policy.policy_id, signals=signals)
    orders = service.create_order_plans(portfolio_plan_id=plan.plan_id)

    with pytest.raises(ApprovalRequired):
        service.submit_order_plan(orders[0].order_plan_id)


def test_duplicate_idempotency_key_is_rejected() -> None:
    policy = parse_policy_text("fixture")
    order_plan = sample_order_plan(policy)
    risk_check = run_risk_check(
        policy=policy,
        order_plan=order_plan,
        snapshot=fixture_portfolio_snapshot(),
        seen_idempotency_keys={order_plan.idempotency_key},
    )
    assert not risk_check.passed
    assert "idempotency_key_not_seen" in risk_check.failed_checks


def test_monthly_loss_pause_blocks_new_buys() -> None:
    policy = parse_policy_text("fixture")
    order_plan = sample_order_plan(policy)
    risk_check = run_risk_check(
        policy=policy,
        order_plan=order_plan,
        snapshot=fixture_portfolio_snapshot(monthly_loss_ratio=-0.06),
    )
    assert not risk_check.passed
    assert "monthly_loss_pause_not_triggered" in risk_check.failed_checks


def test_monthly_loss_stop_disables_automatic_trading() -> None:
    policy = parse_policy_text("fixture")
    order_plan = sample_order_plan(policy)
    risk_check = run_risk_check(
        policy=policy,
        order_plan=order_plan,
        snapshot=fixture_portfolio_snapshot(monthly_loss_ratio=-0.11),
    )
    assert not risk_check.passed
    assert "monthly_loss_stop_not_triggered" in risk_check.failed_checks


def test_market_orders_are_blocked_by_default() -> None:
    policy = UserPolicy(allowed_order_types=[OrderType.market])
    order_plan = sample_order_plan(policy, order_type=OrderType.market)
    risk_check = run_risk_check(policy=policy, order_plan=order_plan, snapshot=fixture_portfolio_snapshot())
    assert not risk_check.passed
    assert "order_type_allowed" in risk_check.failed_checks


def test_stale_quotes_are_rejected() -> None:
    policy = parse_policy_text("fixture")
    order_plan = sample_order_plan(policy)
    order_plan.intent.quote_time = utc_now() - timedelta(minutes=30)
    risk_check = run_risk_check(policy=policy, order_plan=order_plan, snapshot=fixture_portfolio_snapshot())
    assert "quote_not_stale" in risk_check.failed_checks


def test_mock_broker_completes_account_order_fill_flow() -> None:
    policy = parse_policy_text("fixture")
    order_plan = sample_order_plan(policy)
    broker = MockBroker()
    account = broker.get_account(policy.user_id)
    broker_order, fills = broker.submit_order(order_plan)

    assert account["broker_mode"] == BrokerMode.mock.value
    assert broker_order.broker_mode == BrokerMode.mock
    assert len(fills) == 1
    assert fills[0].order_plan_id == order_plan.order_plan_id


def test_paper_broker_never_calls_live_broker_apis() -> None:
    policy = parse_policy_text("paper")
    order_plan = sample_order_plan(policy)
    broker = PaperBroker()
    broker_order, fills = broker.submit_order(order_plan)

    assert broker.live_api_calls == 0
    assert broker_order.broker_mode == BrokerMode.paper
    assert fills


def test_audit_logs_are_emitted_on_state_transitions() -> None:
    repositories = RepositoryRegistry()
    audit = AuditRecorder(repositories.audit_logs)
    policy = parse_policy_text("fixture")
    order_plan = sample_order_plan(policy)

    transition_order_plan(
        order_plan=order_plan,
        new_status=OrderStatus.rejected,
        audit=audit,
        user_id=policy.user_id,
        source="test",
    )

    events = repositories.audit_logs.list()
    assert len(events) == 1
    assert events[0].action == "order_rejected"


def test_fable5_recipe_cannot_directly_submit_an_order() -> None:
    with pytest.raises(DirectOrderSubmissionBlocked):
        submit_order_from_fable5_recipe("submit this order")
