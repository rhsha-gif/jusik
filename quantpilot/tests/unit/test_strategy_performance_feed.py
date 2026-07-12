from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

from quantpilot.packages.core.backtest.costs import (
    KIS_BANKIS_ONLINE_FEE_BPS,
    KIS_RETAIL_COST_BASIS,
    KRX_SELL_TAX_BPS_FROM_2026,
)
from quantpilot.packages.core.backtest import BacktestRequest, BacktestSignal, run_backtest
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import (
    Fill,
    OrderIntent,
    OrderPlan,
    ProposalExplanation,
    SignalAction,
    StrategyApprovalTicketStatus,
)

FEE_RATE = KIS_BANKIS_ONLINE_FEE_BPS / 10_000.0
SELL_TAX_RATE = KRX_SELL_TAX_BPS_FROM_2026 / 10_000.0


class StubMarketDataProvider:
    """Deterministic price history for revaluation tests; bars unused here."""

    def __init__(self, price_history: list[dict[str, Any]]):
        self._price_history = price_history

    def get_bars(self) -> list[dict[str, Any]]:
        return []

    def get_price_history(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._price_history]


def _history(closes: dict[str, float], symbol: str = "AAA") -> list[dict[str, Any]]:
    return [
        {
            "symbol": symbol,
            "date": session,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 10_000,
        }
        for session, close in closes.items()
    ]


def _seed_fill(
    service: HarnessService,
    *,
    side: str,
    quantity: float,
    price: float,
    filled_on: date,
    symbol: str = "AAA",
    strategy_id: str = "strat_alpha",
    strategy_version: str = "1.0",
) -> None:
    notional = quantity * price
    plan = OrderPlan(
        policy_id="pol_test",
        policy_version=1,
        intent=OrderIntent(
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            quantity=quantity,
            limit_price=price,
            notional=notional,
            target_weight=0.1,
            reason="test",
        ),
        idempotency_key=f"{symbol}-{side}-{filled_on.isoformat()}-{price}",
        explanation=ProposalExplanation(
            symbol=symbol,
            action=side,  # type: ignore[arg-type]
            quantity=quantity,
            target_weight_delta=0.1,
            reference_price=price,
            estimated_cash_impact=-notional,
            estimated_notional=notional,
            idempotency_key=f"{symbol}-{side}-{filled_on.isoformat()}-{price}",
            policy_version=1,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            signal_reason="test",
            current_weight=0.0,
            target_weight=0.1,
            weight_delta=0.1,
            quote_price=price,
            quote_age_seconds=0.0,
        ),
    )
    service.repositories.order_plans.add(plan)
    service.repositories.fills.add(
        Fill(
            broker_order_id=f"bo-{plan.order_plan_id}",
            order_plan_id=plan.order_plan_id,
            symbol=symbol,
            quantity=quantity,
            price=price,
            notional=notional,
            filled_at=datetime(
                filled_on.year, filled_on.month, filled_on.day, 10, 0, tzinfo=timezone.utc
            ),
        )
    )


def test_round_trip_return_reflects_fee_and_sell_tax_drag() -> None:
    provider = StubMarketDataProvider(_history({"2026-01-05": 100.0, "2026-01-06": 100.0}))
    service = HarnessService(market_data_provider=provider)
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))
    _seed_fill(service, side="sell", quantity=10, price=100.0, filled_on=date(2026, 1, 6))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    expected_return = -(2 * FEE_RATE + SELL_TAX_RATE)
    assert record.realized_total_return == pytest.approx(expected_return)
    assert record.realized_total_return < 0
    assert record.realized_max_drawdown == pytest.approx(abs(expected_return))


def test_interim_close_drawdown_is_observed_without_a_sell_fill() -> None:
    provider = StubMarketDataProvider(
        _history({"2026-01-05": 100.0, "2026-01-06": 90.0, "2026-01-07": 100.0})
    )
    service = HarnessService(market_data_provider=provider)
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    # The fill-price-only model saw no drawdown here; the 90 close is a 10%
    # dip on 1000 invested (plus the buy commission drag) that the drift
    # monitor must observe.
    assert record.realized_max_drawdown == pytest.approx(0.1 + FEE_RATE)
    assert record.observation_days == 3


def test_missing_price_history_falls_back_to_fill_prices() -> None:
    provider = StubMarketDataProvider([])
    service = HarnessService(market_data_provider=provider)
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))
    _seed_fill(service, side="sell", quantity=10, price=110.0, filled_on=date(2026, 1, 6))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    # buy fee on 1000 notional, sell fee+tax on 1100 notional, no revaluation rows
    expected = 0.1 - FEE_RATE - (FEE_RATE + SELL_TAX_RATE) * 1.1
    assert record.realized_total_return == pytest.approx(expected)


def test_record_documents_cost_basis_and_valuation() -> None:
    provider = StubMarketDataProvider(_history({"2026-01-05": 100.0}))
    service = HarnessService(market_data_provider=provider)
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.cost_basis == KIS_RETAIL_COST_BASIS
    assert record.valuation == "daily_close"
    assert record.source == "auto_feed"


def test_ticker_key_and_lowercase_symbols_normalize_for_revaluation() -> None:
    rows = [
        {"ticker": "aaa", "date": "2026-01-05", "close": 100.0},
        {"ticker": "aaa", "date": "2026-01-06", "close": 80.0},
    ]
    service = HarnessService(market_data_provider=StubMarketDataProvider(rows))
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.realized_max_drawdown == pytest.approx(0.2, rel=1e-3)


def test_zero_mdd_evidence_ticket_expires_on_first_fee_bearing_fill() -> None:
    """Documents intended fail-closed behavior (review finding F1).

    Backtest evidence marks equity at session closes only, so a
    monotonically rising run yields exactly 0.0 MDD and a drift limit of 0.
    The fee-aware feed realizes a commission-sized drawdown on the very
    first buy fill, so such a ticket expires immediately: zero-MDD evidence
    gives the monitor no tolerable drawdown budget to operate within.
    """
    provider = StubMarketDataProvider(_history({"2026-01-05": 100.0}))
    service = HarnessService(market_data_provider=provider)
    evidence = run_backtest(
        BacktestRequest(
            strategy_id="strat_alpha",
            recipe_version="1.0",
            initial_cash=10_000,
            signals=[
                BacktestSignal(
                    symbol="AAA",
                    signal_date=date(2026, 1, 1),
                    action=SignalAction.buy_ready,
                    target_weight_hint=0.5,
                    reason="rising evidence run",
                )
            ],
        ),
        _history(
            {f"2026-01-0{day}": 100.0 + 2 * day for day in range(1, 7)}
        ),
    )
    assert evidence.metrics.max_drawdown == 0.0
    service.record_backtest_result(evidence)
    ticket = service.create_strategy_approval_ticket(
        strategy_id="strat_alpha",
        strategy_version="1.0",
        spec_hash="hash_1",
        backtest_report_id=evidence.result_id,
    )
    service.approve_strategy_ticket(ticket.ticket_id)
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    records = service.run_strategy_performance_feed()
    allowed, _ = service.strategy_activation_allowed("strat_alpha", execution_level="level_3")

    assert len(records) == 1
    assert records[0].realized_max_drawdown == pytest.approx(FEE_RATE)
    assert records[0].realized_max_drawdown > 0
    assert allowed is False
    stored = service.repositories.strategy_approval_tickets.require(ticket.ticket_id)
    assert stored.status == StrategyApprovalTicketStatus.expired
    assert "mdd_exceeds_backtest_1_5x" in stored.reapproval_triggers


def test_flat_days_after_full_exit_do_not_extend_observation() -> None:
    provider = StubMarketDataProvider(
        _history({"2026-01-05": 100.0, "2026-01-06": 100.0, "2026-01-07": 100.0})
    )
    service = HarnessService(market_data_provider=provider)
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))
    _seed_fill(service, side="sell", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    # Both fills landed on one day and the position closed; later flat close
    # days add no information and must not inflate observation_days.
    assert record.observation_days == 1
