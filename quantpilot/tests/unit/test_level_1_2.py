from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from quantpilot.packages.core.analyst.reports import generate_analyst_report
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.policy.parser import parse_policy_text
from quantpilot.packages.core.portfolio.planner import build_rebalance_suggestion_report, fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import SignalAction, TechnicalIndicatorSnapshot, UserPolicy
from quantpilot.packages.core.signals.service import classify_level2_action, generate_signals, load_fixture_ohlcv
from quantpilot.packages.core.strategies.loader import load_default_strategy
from quantpilot.packages.core.technical.indicators import calculate_technical_indicators
from quantpilot.packages.core.universe.builder import build_candidate_universe
from quantpilot.services.api.main import app


def _indicator(
    *,
    close: float = 105.0,
    ma20: float = 100.0,
    rsi: float = 45.0,
    technical_score: float = 75.0,
    quant_score: float = 70.0,
) -> TechnicalIndicatorSnapshot:
    return TechnicalIndicatorSnapshot(
        ticker="AAA",
        signal_date=date(2026, 6, 10),
        close=close,
        moving_averages={"ma5": close, "ma20": ma20},
        returns={"return_1d": 0.02, "return_5d": 0.08},
        volatility=0.10,
        rsi=rsi,
        volume_ratio=1.4,
        momentum_score=quant_score,
        technical_score=technical_score,
        liquidity_score=80.0,
        defensive_score=60.0,
        data_points=25,
    )


def test_korean_policy_preview_parser_extracts_direction_and_limits() -> None:
    policy = parse_policy_text("미국 AI 반도체 중심으로 보수적으로 투자, 현금 30%, 종목당 10%, AAA 제외")

    assert policy.market == "US_STOCK"
    assert policy.risk_profile == "conservative"
    assert policy.min_cash_weight == 0.30
    assert policy.max_position_weight == 0.10
    assert "AAA" in policy.blocklist
    assert {"ai", "semiconductor"}.issubset(set(policy.preferred_themes))


def test_policy_preview_api_returns_json_without_confirming_policy() -> None:
    response = TestClient(app).post(
        "/api/policies/preview",
        json={"text": "미국 AI 반도체 중심, 현금 30%, 종목당 10%, AAA 제외"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["confirmed"] is False
    assert body["policy"]["market"] == "US_STOCK"
    assert body["policy_json"]["max_position_weight"] == 0.10


def test_universe_builder_respects_policy_blocklist() -> None:
    policy = UserPolicy(blocklist=["AAA"], preferred_themes=["ai", "semiconductor"])
    universe = build_candidate_universe(policy)

    aaa = next(candidate for candidate in universe if candidate.ticker == "AAA")
    assert not aaa.data_ready or aaa.block_reason == "policy_blocklist"
    assert aaa.block_reason == "policy_blocklist"
    assert aaa.analyst_required is False


def test_universe_builder_liquidity_filter_blocks_thin_candidates() -> None:
    policy = UserPolicy(min_avg_daily_value=6_000_000)
    universe = build_candidate_universe(policy)

    fff = next(candidate for candidate in universe if candidate.ticker == "FFF")
    assert fff.liquidity_pass is False
    assert fff.block_reason == "liquidity_below_minimum"
    assert fff.analyst_required is False


def test_analyst_report_does_not_override_signal_action() -> None:
    policy = UserPolicy(preferred_themes=["ai"])
    candidate = next(candidate for candidate in build_candidate_universe(policy) if candidate.ticker == "AAA")
    signal = generate_signals(load_default_strategy(), load_fixture_ohlcv(), policy=policy)[0]
    original_action = signal.action

    report = generate_analyst_report(candidate=candidate, indicator=_indicator(), signal=signal)

    assert signal.action == original_action
    assert report.ticker == signal.ticker
    assert report.rating in {"positive", "neutral", "caution", "blocked"}


def test_technical_indicators_ignore_future_rows_after_signal_date() -> None:
    rows = [
        {"ticker": "AAA", "date": "2026-01-01", "open": 99, "high": 101, "low": 98, "close": 100, "volume": 1000},
        {"ticker": "AAA", "date": "2026-01-02", "open": 100, "high": 103, "low": 99, "close": 102, "volume": 1100},
        {"ticker": "AAA", "date": "2026-01-03", "open": 102, "high": 104, "low": 101, "close": 103, "volume": 1200},
        {"ticker": "AAA", "date": "2026-01-04", "open": 103, "high": 105, "low": 102, "close": 104, "volume": 1300},
    ]
    future_row = {"ticker": "AAA", "date": "2026-01-05", "open": 104, "high": 1000, "low": 103, "close": 999, "volume": 999999}

    without_future = calculate_technical_indicators(rows, ticker="AAA", signal_date=date(2026, 1, 4))
    with_future = calculate_technical_indicators(rows + [future_row], ticker="AAA", signal_date=date(2026, 1, 4))

    assert with_future.close == without_future.close
    assert with_future.moving_averages == without_future.moving_averages
    assert with_future.returns == without_future.returns
    assert with_future.technical_score == without_future.technical_score


def test_signal_classification_precedence_blocks_before_buy_and_exits_before_trim() -> None:
    policy = UserPolicy(max_position_weight=0.10)
    candidate = next(candidate for candidate in build_candidate_universe(policy) if candidate.ticker == "AAA")

    blocked = candidate.model_copy(update={"block_reason": "policy_blocklist", "liquidity_pass": True, "data_ready": True})
    assert classify_level2_action(policy, blocked, _indicator(), current_weight=0.0) == SignalAction.blocked

    exit_indicator = _indicator(close=90.0, ma20=100.0, rsi=75.0, technical_score=80.0)
    assert classify_level2_action(policy, candidate, exit_indicator, current_weight=0.20) == SignalAction.exit


def test_stop_loss_and_take_profit_hints_are_generated_for_signal_board() -> None:
    policy = UserPolicy()
    signals = generate_signals(load_default_strategy(), load_fixture_ohlcv(), policy=policy)

    buy_signal = next(signal for signal in signals if signal.action == SignalAction.buy_ready)
    assert buy_signal.stop_price_hint is not None
    assert buy_signal.take_profit_hint is not None
    assert buy_signal.stop_price_hint < buy_signal.take_profit_hint
    assert buy_signal.valid_until == buy_signal.signal_date + timedelta(days=5)


def test_rebalance_suggestions_respect_policy_limits_without_order_plans() -> None:
    policy = UserPolicy(max_position_weight=0.08, min_cash_weight=0.30)
    signals = generate_signals(load_default_strategy(), load_fixture_ohlcv(), policy=policy)

    report = build_rebalance_suggestion_report(
        policy=policy,
        signals=signals,
        snapshot=fixture_portfolio_snapshot(),
    )

    assert report.portfolio_plan.order_intents == []
    assert report.portfolio_plan.cash_target_weight >= policy.min_cash_weight
    assert all(weight <= policy.max_position_weight for weight in report.portfolio_plan.target_weights.values())
    assert all(item.suggested_action in {"buy", "sell", "hold", "blocked"} for item in report.suggestions)


def test_level_1_2_research_flow_cannot_submit_broker_orders() -> None:
    service = HarnessService()
    policy = service.parse_policy("fixture")

    result = service.run_level_1_2(policy_id=policy.policy_id)

    assert result["order_submission_enabled"] is False
    assert service.repositories.order_plans.list() == []
    assert service.repositories.broker_orders.list() == []
    assert service.repositories.fills.list() == []
