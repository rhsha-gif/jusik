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
from quantpilot.packages.core.data.quality import SimpleKrxCalendar
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
ACCOUNT_EQUITY = 10_000.0


class StubMarketDataProvider:
    """Deterministic price history for revaluation tests; bars unused here."""

    def __init__(self, price_history: list[dict[str, Any]]):
        self._price_history = price_history

    def get_bars(self) -> list[dict[str, Any]]:
        return []

    def get_price_history(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._price_history]


class RaisingMarketDataProvider:
    """Models a transient provider failure at the performance-feed boundary."""

    def get_bars(self) -> list[dict[str, Any]]:
        return []

    def get_price_history(self) -> list[dict[str, Any]]:
        raise RuntimeError("provider unavailable")


class RaisingIteratorMarketDataProvider:
    def get_bars(self) -> list[dict[str, Any]]:
        return []

    def get_price_history(self):  # type: ignore[no-untyped-def]
        def rows():  # type: ignore[no-untyped-def]
            yield {"symbol": "AAA", "date": "2026-01-05", "close": 100.0}
            raise RuntimeError("lazy provider failed")

        return rows()


def _after_krx_close(session: date) -> datetime:
    return datetime(session.year, session.month, session.day, 7, 0, tzinfo=timezone.utc)


def _service(
    provider: StubMarketDataProvider | RaisingMarketDataProvider,
    *,
    evaluated_on: date = date(2026, 1, 7),
) -> HarnessService:
    evaluated_at = _after_krx_close(evaluated_on)
    return HarnessService(
        market_data_provider=provider,
        performance_clock=lambda: evaluated_at,
    )


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
    filled_at: datetime | None = None,
    weight_delta: float = 0.1,
    estimated_notional: float | None = None,
    account_equity: float = ACCOUNT_EQUITY,
) -> OrderPlan:
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
            target_weight_delta=weight_delta,
            reference_price=price,
            estimated_cash_impact=-notional,
            estimated_notional=estimated_notional or notional,
            account_equity_at_proposal=account_equity,
            portfolio_snapshot_id="snap-performance-test",
            idempotency_key=f"{symbol}-{side}-{filled_on.isoformat()}-{price}",
            policy_version=1,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            signal_reason="test",
            current_weight=0.0,
            target_weight=0.1,
            weight_delta=weight_delta,
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
            filled_at=filled_at
            or datetime(
                filled_on.year, filled_on.month, filled_on.day, 1, 0, tzinfo=timezone.utc
            ),
        )
    )
    return plan


def test_proposal_equity_evidence_requires_snapshot_id() -> None:
    service = _service(StubMarketDataProvider([]))
    plan = _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
    )
    payload = plan.explanation.model_dump()  # type: ignore[union-attr]
    payload["portfolio_snapshot_id"] = None

    with pytest.raises(ValueError, match="requires both"):
        ProposalExplanation.model_validate(payload)


def test_round_trip_return_reflects_fee_and_sell_tax_drag() -> None:
    provider = StubMarketDataProvider(_history({"2026-01-05": 100.0, "2026-01-06": 100.0}))
    service = _service(provider)
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))
    _seed_fill(service, side="sell", quantity=10, price=100.0, filled_on=date(2026, 1, 6))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    expected_return = -0.1 * (2 * FEE_RATE + SELL_TAX_RATE)
    assert record.realized_total_return == pytest.approx(expected_return)
    assert record.realized_total_return < 0
    assert record.realized_max_drawdown == pytest.approx(abs(expected_return))


def test_interim_close_drawdown_is_observed_without_a_sell_fill() -> None:
    provider = StubMarketDataProvider(
        _history({"2026-01-05": 100.0, "2026-01-06": 90.0, "2026-01-07": 100.0})
    )
    service = _service(provider)
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    # The fill-price-only model saw no drawdown here; the 90 close is a 10%
    # dip on 1000 invested (plus the buy commission drag) that the drift
    # monitor must observe.
    assert record.realized_max_drawdown == pytest.approx(0.01 + 0.1 * FEE_RATE)
    assert record.observation_days == 3


def test_missing_price_history_falls_back_to_fill_prices() -> None:
    provider = StubMarketDataProvider([])
    service = _service(provider)
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))
    _seed_fill(service, side="sell", quantity=10, price=110.0, filled_on=date(2026, 1, 6))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    # buy fee on 1000 notional, sell fee+tax on 1100 notional, no revaluation rows
    expected = (100.0 - 1_000 * FEE_RATE - 1_100 * (FEE_RATE + SELL_TAX_RATE)) / ACCOUNT_EQUITY
    assert record.realized_total_return == pytest.approx(expected)
    assert record.valuation == "last_fill_price"


def test_provider_failure_falls_back_without_stopping_drift_feed() -> None:
    service = _service(RaisingMarketDataProvider(), evaluated_on=date(2026, 1, 5))
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.realized_max_drawdown == pytest.approx(0.1 * FEE_RATE)
    assert record.valuation == "last_fill_price"
    assert record.valuation_status == "provider_error"


def test_lazy_provider_failure_is_caught_during_materialization() -> None:
    service = HarnessService(
        market_data_provider=RaisingIteratorMarketDataProvider(),
        performance_clock=lambda: _after_krx_close(date(2026, 1, 5)),
    )
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.valuation_status == "provider_error"


def test_non_finite_close_is_ignored_as_degraded_input() -> None:
    provider = StubMarketDataProvider(
        [{"symbol": "AAA", "date": "2026-01-05", "close": float("inf")}]
    )
    service = _service(provider, evaluated_on=date(2026, 1, 5))
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.realized_max_drawdown == pytest.approx(0.1 * FEE_RATE)
    assert record.valuation == "last_fill_price"


def test_turnover_does_not_dilute_final_position_drawdown() -> None:
    sessions = [
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
        date(2026, 1, 12),
        date(2026, 1, 13),
        date(2026, 1, 14),
        date(2026, 1, 15),
        date(2026, 1, 16),
    ]
    closes = {
        session.isoformat(): 100.0
        for session in sessions
    }
    closes[sessions[-1].isoformat()] = 90.0
    service = _service(
        StubMarketDataProvider(_history(closes)), evaluated_on=sessions[-1]
    )
    for session in sessions[:-1]:
        _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=session)
        _seed_fill(service, side="sell", quantity=10, price=100.0, filled_on=session)
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=sessions[-1],
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.realized_max_drawdown == pytest.approx(0.0120670013)


def test_fill_symbols_are_canonicalized_before_position_accounting() -> None:
    service = _service(StubMarketDataProvider([]))
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
        symbol="aaa",
    )
    _seed_fill(
        service,
        side="sell",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 6),
        symbol="AAA",
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.realized_total_return == pytest.approx(
        -0.1 * (2 * FEE_RATE + SELL_TAX_RATE)
    )


def test_future_and_current_incomplete_krx_sessions_are_excluded() -> None:
    evaluated_at = datetime(2026, 1, 7, 0, 0, tzinfo=timezone.utc)  # 09:00 KST
    provider = StubMarketDataProvider(
        _history(
            {
                "2026-01-05": 100.0,
                "2026-01-06": 90.0,
                "2026-01-07": 1.0,
                "2026-01-08": 1.0,
            }
        )
    )
    service = HarnessService(
        market_data_provider=provider,
        performance_clock=lambda: evaluated_at,
    )
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
        filled_at=datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc),
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.realized_total_return == pytest.approx(
        -0.01 - 0.1 * FEE_RATE, rel=1e-3
    )
    assert record.as_of == evaluated_at


def test_fill_and_close_use_the_same_kst_session_date() -> None:
    provider = StubMarketDataProvider(_history({"2026-01-05": 90.0}))
    service = _service(provider, evaluated_on=date(2026, 1, 5))
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
        filled_at=datetime(2026, 1, 4, 23, 30, tzinfo=timezone.utc),
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.observation_days == 1
    assert record.realized_total_return == pytest.approx(
        -0.01 - 0.1 * FEE_RATE, rel=1e-3
    )


def test_missing_close_for_any_open_symbol_marks_valuation_degraded() -> None:
    service = _service(
        StubMarketDataProvider(_history({"2026-01-05": 100.0}, "AAA")),
        evaluated_on=date(2026, 1, 5),
    )
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
        symbol="AAA",
    )
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
        symbol="BBB",
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.valuation == "last_fill_price"
    assert record.valuation_status == "stale"


def test_stale_open_position_close_marks_record_stale() -> None:
    service = _service(
        StubMarketDataProvider(_history({"2026-01-05": 100.0})),
        evaluated_on=date(2026, 1, 8),
    )
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.valuation_status == "stale"
    assert record.market_data_as_of_session == date(2026, 1, 5)


def test_future_fill_requires_reconciliation_and_is_not_valued() -> None:
    service = _service(StubMarketDataProvider([]), evaluated_on=date(2026, 1, 5))
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 6),
        filled_at=datetime(2026, 1, 6, 1, 0, tzinfo=timezone.utc),
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.valuation_status == "reconciliation_required"
    assert record.normalization_basis == "reconciliation_required"
    assert record.realized_total_return == 0.0


def test_sell_beyond_attributed_inventory_requires_reconciliation() -> None:
    service = _service(StubMarketDataProvider([]))
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
    )
    _seed_fill(
        service,
        side="sell",
        quantity=20,
        price=100.0,
        filled_on=date(2026, 1, 6),
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.valuation_status == "reconciliation_required"
    assert record.normalization_basis == "reconciliation_required"
    assert record.realized_total_return == pytest.approx(
        -0.1 * (2 * FEE_RATE + SELL_TAX_RATE)
    )


def test_normal_nav_change_does_not_change_first_order_equity_basis() -> None:
    provider = StubMarketDataProvider(
        _history({"2026-01-05": 100.0, "2026-01-06": 100.0})
    )
    service = _service(provider, evaluated_on=date(2026, 1, 6))
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
        weight_delta=0.1,
    )
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 6),
        weight_delta=0.05,
        account_equity=10_200,
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.valuation_status == "complete"
    assert record.normalization_basis == "first_order_account_equity"
    assert record.normalization_equity == ACCOUNT_EQUITY
    assert record.normalization_snapshot_id == "snap-performance-test"


def test_record_documents_cost_basis_and_valuation() -> None:
    provider = StubMarketDataProvider(_history({"2026-01-05": 100.0}))
    service = _service(provider, evaluated_on=date(2026, 1, 5))
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.cost_basis == KIS_RETAIL_COST_BASIS
    assert record.valuation == "daily_close"
    assert record.normalization_basis == "first_order_account_equity"
    assert record.normalization_equity == ACCOUNT_EQUITY
    assert record.normalization_snapshot_id == "snap-performance-test"
    assert record.valuation_status == "complete"
    assert record.source == "auto_feed"
    assert record.included_fill_count == 1
    assert record.included_fill_fingerprint is not None
    assert record.calendar_name == "simple_krx"
    assert record.valuation_start_session == date(2026, 1, 5)
    assert record.calendar_as_of_session == date(2026, 1, 5)
    assert record.calendar_fingerprint is not None


def test_ticker_key_and_lowercase_symbols_normalize_for_revaluation() -> None:
    rows = [
        {"ticker": "aaa", "date": "2026-01-05", "close": 100.0},
        {"ticker": "aaa", "date": "2026-01-06", "close": 80.0},
    ]
    service = _service(StubMarketDataProvider(rows), evaluated_on=date(2026, 1, 6))
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.realized_max_drawdown == pytest.approx(
        0.02 + 0.1 * FEE_RATE, rel=1e-3
    )


def test_zero_mdd_evidence_ticket_expires_on_first_fee_bearing_fill() -> None:
    """Documents intended fail-closed behavior (review finding F1).

    Backtest evidence marks equity at session closes only, so a
    monotonically rising run yields exactly 0.0 MDD and a drift limit of 0.
    The fee-aware feed realizes a commission-sized drawdown on the very
    first buy fill, so such a ticket expires immediately: zero-MDD evidence
    gives the monitor no tolerable drawdown budget to operate within.
    """
    provider = StubMarketDataProvider(_history({"2026-01-05": 100.0}))
    service = _service(provider, evaluated_on=date(2026, 1, 5))
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
    assert records[0].realized_max_drawdown == pytest.approx(0.1 * FEE_RATE)
    assert records[0].realized_max_drawdown > 0
    assert allowed is False
    stored = service.repositories.strategy_approval_tickets.require(ticket.ticket_id)
    assert stored.status == StrategyApprovalTicketStatus.expired
    assert "mdd_exceeds_backtest_1_5x" in stored.reapproval_triggers


def test_flat_days_after_full_exit_do_not_extend_observation() -> None:
    provider = StubMarketDataProvider(
        _history({"2026-01-05": 100.0, "2026-01-06": 100.0, "2026-01-07": 100.0})
    )
    service = _service(provider)
    _seed_fill(service, side="buy", quantity=10, price=100.0, filled_on=date(2026, 1, 5))
    _seed_fill(service, side="sell", quantity=10, price=100.0, filled_on=date(2026, 1, 5))

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    # Both fills landed on one day and the position closed; later flat close
    # days add no information and must not inflate observation_days.
    assert record.observation_days == 1


def test_post_close_fill_cannot_use_an_earlier_same_day_close() -> None:
    evaluated_at = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)  # 17:00 KST
    service = HarnessService(
        market_data_provider=StubMarketDataProvider(
            _history({"2026-01-05": 200.0})
        ),
        performance_clock=lambda: evaluated_at,
    )
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
        filled_at=datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc),
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.valuation_status == "fill_only"
    assert record.market_data_as_of_session is None
    assert record.realized_total_return == pytest.approx(-0.1 * FEE_RATE)


def test_current_day_close_is_not_final_before_provider_delay() -> None:
    evaluated_at = datetime(2026, 1, 5, 6, 45, tzinfo=timezone.utc)  # 15:45 KST
    service = HarnessService(
        market_data_provider=StubMarketDataProvider(
            _history({"2026-01-05": 50.0})
        ),
        performance_clock=lambda: evaluated_at,
    )
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2026, 1, 5),
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.valuation_status == "fill_only"
    assert record.realized_total_return == pytest.approx(-0.1 * FEE_RATE)


def test_configured_krx_holiday_is_not_treated_as_missing_close() -> None:
    evaluated_at = datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc)
    service = HarnessService(
        market_data_provider=StubMarketDataProvider(
            _history({"2025-12-31": 100.0, "2026-01-02": 100.0})
        ),
        performance_clock=lambda: evaluated_at,
        performance_calendar=SimpleKrxCalendar(
            holidays=(date(2026, 1, 1),)
        ),
    )
    _seed_fill(
        service,
        side="buy",
        quantity=10,
        price=100.0,
        filled_on=date(2025, 12, 31),
    )

    record = service.compute_strategy_performance("strat_alpha", "1.0")

    assert record is not None
    assert record.valuation_status == "complete"
    assert record.market_data_as_of_session == date(2026, 1, 2)
    assert record.market_data_fingerprint is not None
    assert record.market_data_close_count == 2
