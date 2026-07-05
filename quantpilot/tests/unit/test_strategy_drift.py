from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from quantpilot.packages.core.backtest import BacktestRequest, BacktestSignal, run_backtest
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import (
    SignalAction,
    StrategyApprovalTicketStatus,
    StrategyPerformanceRecord,
)


def _rows(days: int = 6) -> list[dict[str, Any]]:
    start = date(2026, 1, 1)
    rows: list[dict[str, Any]] = []
    for index in range(days):
        session = start + timedelta(days=index)
        close = 100.0 + index * 2
        rows.append(
            {
                "symbol": "AAA",
                "date": session.isoformat(),
                "open": close - 1,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": 100_000,
            }
        )
    return rows


def _approved_ticket(service: HarnessService):
    result = run_backtest(
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
                    reason="evidence run",
                )
            ],
        ),
        _rows(),
    )
    service.record_backtest_result(result)
    ticket = service.create_strategy_approval_ticket(
        strategy_id="strat_alpha",
        strategy_version="1.0",
        spec_hash="hash_1",
        backtest_report_id=result.result_id,
    )
    return service.approve_strategy_ticket(ticket.ticket_id)


def _performance(mdd: float) -> StrategyPerformanceRecord:
    return StrategyPerformanceRecord(
        strategy_id="strat_alpha",
        strategy_version="1.0",
        realized_max_drawdown=mdd,
        realized_total_return=0.01,
        observation_days=10,
    )


def test_no_performance_record_keeps_activation_open() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)

    allowed, detail = service.strategy_activation_allowed("strat_alpha", execution_level="level_3")

    assert allowed is True
    assert detail == ticket.ticket_id


def test_realized_mdd_within_limit_keeps_activation_open() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)
    # The rising-price evidence run has zero MDD; a zero realized MDD does not
    # exceed limit 0.0 and must not fire the trigger.
    service.record_strategy_performance(_performance(mdd=0.0))

    allowed, _ = service.strategy_activation_allowed("strat_alpha", execution_level="level_3")

    assert allowed is True
    stored = service.repositories.strategy_approval_tickets.require(ticket.ticket_id)
    assert stored.status == StrategyApprovalTicketStatus.approved


def test_mdd_drift_expires_ticket_and_closes_gate() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)
    # Zero-MDD evidence means ANY realized drawdown exceeds the 1.5x multiple
    # (fail-closed); this also covers the general realized > limit case.
    service.record_strategy_performance(_performance(mdd=0.05))

    allowed, detail = service.strategy_activation_allowed("strat_alpha", execution_level="level_3")

    assert allowed is False
    assert detail == "no_active_strategy_approval"
    stored = service.repositories.strategy_approval_tickets.require(ticket.ticket_id)
    assert stored.status == StrategyApprovalTicketStatus.expired
    assert "mdd_exceeds_backtest_1_5x" in stored.reapproval_triggers
    drift_events = [
        event
        for event in service.repositories.audit_logs.list()
        if event.action == "strategy_ticket_drift_expired"
    ]
    assert len(drift_events) == 1
