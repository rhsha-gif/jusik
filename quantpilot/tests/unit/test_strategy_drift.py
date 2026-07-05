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


def test_auto_feed_attributes_fills_and_records_performance() -> None:
    service = HarnessService()
    policy = service.parse_policy("fixture")
    for ticket in service.generate_approval_tickets(policy_id=policy.policy_id, data_mode="fixture"):
        service.approve_and_submit_approval_ticket(ticket.ticket_id)
    assert service.repositories.fills.list(), "expected mock fills to attribute"

    records = service.run_strategy_performance_feed()

    assert records, "expected at least one strategy performance record"
    for record in records:
        assert record.source == "auto_feed"
        assert record.observation_days >= 1
        assert record.realized_max_drawdown >= 0
        stored = service.repositories.strategy_performance.require(record.record_id)
        assert stored.strategy_id == record.strategy_id


def test_auto_feed_is_empty_without_fills() -> None:
    service = HarnessService()

    assert service.run_strategy_performance_feed() == []


def test_kill_switch_revokes_armed_strategies_and_closes_gate() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)
    policy = service.parse_policy("fixture")
    assert service.strategy_activation_allowed("strat_alpha", execution_level="level_3")[0] is True

    service.engage_kill_switch(policy_id=policy.policy_id, reason="test")

    allowed, detail = service.strategy_activation_allowed("strat_alpha", execution_level="level_3")
    assert allowed is False
    assert detail == "kill_switch_engaged"
    stored = service.repositories.strategy_approval_tickets.require(ticket.ticket_id)
    assert stored.status == StrategyApprovalTicketStatus.revoked
    assert stored.revoked_reason == "kill_switch_engaged"

    # Releasing the switch does NOT re-arm strategies: a fresh approval is required.
    service.release_kill_switch(policy_id=policy.policy_id, confirmation="release kill switch")
    assert service.strategy_activation_allowed("strat_alpha", execution_level="level_3")[0] is False


def test_safety_events_land_in_the_notification_inbox() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)
    service.record_strategy_performance(_performance(mdd=0.05))
    service.strategy_activation_allowed("strat_alpha", execution_level="level_3")  # fires drift

    inbox = service.list_notifications(unacknowledged_only=True)

    assert [item.event_type for item in inbox] == ["strategy_drift_expired"]
    drift_note = inbox[0]
    assert drift_note.severity == "critical"
    assert drift_note.ticket_id == ticket.ticket_id

    acknowledged = service.acknowledge_notification(drift_note.notification_id)
    assert acknowledged.acknowledged_at is not None
    assert service.list_notifications(unacknowledged_only=True) == []


def test_kill_switch_emits_critical_notifications() -> None:
    service = HarnessService()
    _approved_ticket(service)
    policy = service.parse_policy("fixture")

    service.engage_kill_switch(policy_id=policy.policy_id, reason="test")

    events = [item.event_type for item in service.list_notifications()]
    assert "kill_switch_engaged" in events
    assert "strategy_ticket_revoked" in events


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
