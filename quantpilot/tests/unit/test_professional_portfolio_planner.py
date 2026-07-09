from __future__ import annotations

from datetime import datetime, timezone

from quantpilot.packages.core.portfolio.planner import build_portfolio_plan
from quantpilot.packages.core.schemas import (
    PortfolioPosition,
    PortfolioSnapshot,
    Signal,
    SignalAction,
    UserPolicy,
)


QUOTE_TIME = datetime(2026, 7, 10, 1, 30, tzinfo=timezone.utc)


def _policy(*, max_sector_weight: float = 0.60) -> UserPolicy:
    return UserPolicy(
        max_position_weight=0.20,
        max_sector_weight=max_sector_weight,
        min_cash_weight=0.20,
        max_daily_turnover=1_000_000,
        single_order_cash_limit=1_000_000,
    )


def _signal(symbol: str, *, strength: float = 1.0) -> Signal:
    return Signal(
        strategy_id="pullback_trend_v2",
        recipe_version="2",
        symbol=symbol,
        action=SignalAction.buy_ready,
        strength=strength,
        reason="professional planner test",
        source="local_historical",
    )


def _snapshot(*, positions: list[PortfolioPosition] | None = None) -> PortfolioSnapshot:
    active_positions = positions or []
    position_value = sum(position.market_value for position in active_positions)
    return PortfolioSnapshot(
        cash=1_000_000 - position_value,
        equity=1_000_000,
        positions=active_positions,
    )


def test_professional_plan_preserves_quote_price_and_timestamp() -> None:
    plan = build_portfolio_plan(
        policy=_policy(),
        signals=[_signal("AAA")],
        snapshot=_snapshot(),
        quotes={"AAA": 123.45},
        quote_times={"AAA": QUOTE_TIME},
        require_explicit_quotes=True,
        rebalance_band=0.01,
    )

    assert len(plan.order_intents) == 1
    assert plan.order_intents[0].limit_price == 123.45
    assert plan.order_intents[0].quote_time == QUOTE_TIME


def test_professional_plan_fails_closed_per_symbol_when_quote_evidence_is_missing() -> None:
    policy = _policy(max_sector_weight=0.20)
    valid_only = build_portfolio_plan(
        policy=policy,
        signals=[_signal("AAA")],
        snapshot=_snapshot(),
        quotes={"AAA": 101.0},
        quote_times={"AAA": QUOTE_TIME},
        require_explicit_quotes=True,
        rebalance_band=0.01,
    )
    plan = build_portfolio_plan(
        policy=policy,
        signals=[_signal("AAA"), _signal("BBB")],
        snapshot=_snapshot(),
        quotes={"AAA": 101.0},
        quote_times={"AAA": QUOTE_TIME},
        require_explicit_quotes=True,
        rebalance_band=0.01,
    )

    assert [intent.symbol for intent in plan.order_intents] == ["AAA"]
    assert plan.target_weights["AAA"] == valid_only.target_weights["AAA"]
    assert plan.target_weights["BBB"] == 0.0


def test_professional_plan_rejects_non_positive_quote_and_naive_timestamp() -> None:
    plan = build_portfolio_plan(
        policy=_policy(),
        signals=[_signal("AAA"), _signal("BBB")],
        snapshot=_snapshot(),
        quotes={"AAA": 0.0, "BBB": 99.0},
        quote_times={
            "AAA": QUOTE_TIME,
            "BBB": QUOTE_TIME.replace(tzinfo=None),
        },
        require_explicit_quotes=True,
        rebalance_band=0.01,
    )

    assert plan.order_intents == []
    assert plan.target_weights == {"AAA": 0.0, "BBB": 0.0}


def test_legacy_plan_retains_missing_quote_fallback() -> None:
    plan = build_portfolio_plan(
        policy=_policy(),
        signals=[_signal("AAA")],
        snapshot=_snapshot(),
    )

    assert len(plan.order_intents) == 1
    assert plan.order_intents[0].limit_price == 100.0


def test_one_percentage_point_band_keeps_exact_boundary_actionable() -> None:
    exact_boundary = build_portfolio_plan(
        policy=_policy(),
        signals=[_signal("AAA", strength=0.01)],
        snapshot=_snapshot(),
        quotes={"AAA": 100.0},
        quote_times={"AAA": QUOTE_TIME},
        require_explicit_quotes=True,
        rebalance_band=0.01,
    )
    below_boundary = build_portfolio_plan(
        policy=_policy(),
        signals=[_signal("AAA", strength=0.01)],
        snapshot=_snapshot(
            positions=[PortfolioPosition(symbol="AAA", quantity=50, market_price=100)]
        ),
        quotes={"AAA": 100.0},
        quote_times={"AAA": QUOTE_TIME},
        require_explicit_quotes=True,
        rebalance_band=0.01,
    )

    assert exact_boundary.target_weights["AAA"] == 0.01
    assert len(exact_boundary.order_intents) == 1
    assert exact_boundary.order_intents[0].notional == 10_000.0
    assert below_boundary.target_weights["AAA"] == 0.005
    assert below_boundary.order_intents == []
