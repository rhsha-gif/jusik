from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from quantpilot.packages.core.backtest import BacktestRequest, BacktestSignal, run_backtest
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.data.quality import SimpleKrxCalendar
from quantpilot.packages.core.schemas import (
    DataMode,
    Fill,
    OrderIntent,
    OrderPlan,
    ProposalExplanation,
    SignalAction,
    StrategyApprovalTicketStatus,
    StrategyPerformanceRecord,
)


def _ready_auto_record(
    service: HarnessService,
    *,
    mdd: float = 0.0,
    as_of: datetime | None = None,
) -> StrategyPerformanceRecord:
    evidence = service._strategy_fill_evidence("strat_alpha", "1.0")
    return StrategyPerformanceRecord(
        strategy_id="strat_alpha",
        strategy_version="1.0",
        as_of=as_of or datetime(2026, 1, 6, tzinfo=timezone.utc),
        realized_max_drawdown=mdd,
        realized_total_return=0.0,
        observation_days=1,
        source="auto_feed",
        valuation="daily_close",
        valuation_status="complete",
        normalization_basis="first_order_account_equity",
        normalization_equity=10_000,
        normalization_snapshot_id="snap-ready-auto",
        data_mode=DataMode.fixture,
        included_fill_count=len(evidence),
        included_fill_fingerprint=service._strategy_fill_fingerprint(evidence),
        calendar_name=service.performance_calendar.name,
        valuation_start_session=date(2026, 1, 5),
        calendar_as_of_session=date(2026, 1, 6),
        calendar_fingerprint=service._performance_calendar_fingerprint(
            date(2026, 1, 5),
            date(2026, 1, 6),
        ),
        market_data_fingerprint=service._performance_close_fingerprint({}),
        market_data_close_count=0,
    )


class _CloseProvider:
    def __init__(self, close: float) -> None:
        self.close = close

    def get_bars(self) -> list[dict[str, Any]]:
        return []

    def get_price_history(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "AAA",
                "date": "2026-01-05",
                "close": self.close,
            }
        ]


class _UnavailableCloseProvider:
    def get_bars(self) -> list[dict[str, Any]]:
        return []

    def get_price_history(self) -> list[dict[str, Any]]:
        raise RuntimeError("valuation dataset unavailable")


class _FailOnceThenCloseProvider(_CloseProvider):
    def __init__(self, close: float) -> None:
        super().__init__(close)
        self.calls = 0

    def get_price_history(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient valuation failure")
        return super().get_price_history()


def _seed_attributed_fill(
    service: HarnessService,
    *,
    fill_id: str,
    filled_at: datetime,
) -> None:
    plan = OrderPlan(
        policy_id="pol-test",
        policy_version=1,
        intent=OrderIntent(
            symbol="AAA",
            side="buy",
            quantity=10,
            limit_price=100,
            notional=1_000,
            target_weight=0.1,
            reason="drift watermark test",
        ),
        idempotency_key=f"idem-{fill_id}",
        explanation=ProposalExplanation(
            symbol="AAA",
            action="buy",
            quantity=10,
            target_weight_delta=0.1,
            reference_price=100,
            estimated_cash_impact=1_000,
            strategy_id="strat_alpha",
            strategy_version="1.0",
            signal_reason="test",
            current_weight=0.0,
            target_weight=0.1,
            weight_delta=0.1,
            quote_price=100,
            quote_age_seconds=0,
            estimated_notional=1_000,
            account_equity_at_proposal=10_000,
            portfolio_snapshot_id="snap-drift-test",
            idempotency_key=f"idem-{fill_id}",
            policy_version=1,
        ),
    )
    service.repositories.order_plans.add(plan)
    service.repositories.fills.add(
        Fill(
            fill_id=fill_id,
            broker_order_id=f"broker-{fill_id}",
            order_plan_id=plan.order_plan_id,
            symbol="AAA",
            quantity=10,
            price=100,
            notional=1_000,
            filled_at=filled_at,
        )
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


def test_capital_budget_check_passes_without_strategy_ticket() -> None:
    service = HarnessService()

    allowed, detail = service.strategy_capital_budget_check(
        "strat_alpha", proposed_notional=1_000_000, equity=10_000_000
    )

    assert allowed is True
    assert detail == "no_strategy_budget"


def test_capital_budget_blocks_when_proposed_exceeds_ticket_budget() -> None:
    service = HarnessService()
    _approved_ticket(service)  # capital_budget_pct defaults to 0.2

    within, _ = service.strategy_capital_budget_check(
        "strat_alpha", proposed_notional=1_999_999, equity=10_000_000
    )
    over, detail = service.strategy_capital_budget_check(
        "strat_alpha", proposed_notional=2_000_001, equity=10_000_000
    )

    assert within is True
    assert over is False
    assert "strategy_capital_budget_exceeded" in detail


def test_no_performance_record_keeps_activation_open() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)

    allowed, detail = service.strategy_activation_allowed("strat_alpha", execution_level="level_3")

    assert allowed is True
    assert detail == ticket.ticket_id


def test_attributed_fill_without_auto_record_blocks_activation() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)
    _seed_attributed_fill(
        service,
        fill_id="fill-missing-auto",
        filled_at=datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc),
    )

    allowed, detail = service.strategy_activation_allowed(
        "strat_alpha",
        execution_level="level_3",
    )

    assert allowed is False
    assert detail == "strategy_performance_missing"
    assert service.repositories.strategy_approval_tickets.require(ticket.ticket_id).status == (
        StrategyApprovalTicketStatus.approved
    )


def test_degraded_auto_feed_blocks_activation_without_expiring_ticket() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)
    service.record_strategy_performance(
        StrategyPerformanceRecord(
            strategy_id="strat_alpha",
            strategy_version="1.0",
            realized_max_drawdown=0.0,
            realized_total_return=0.0,
            observation_days=1,
            source="auto_feed",
            valuation="last_fill_price",
        )
    )

    allowed, detail = service.strategy_activation_allowed(
        "strat_alpha", execution_level="level_3"
    )

    assert allowed is False
    assert detail == "strategy_performance_valuation_degraded"
    stored = service.repositories.strategy_approval_tickets.require(ticket.ticket_id)
    assert stored.status == StrategyApprovalTicketStatus.approved


def test_newer_manual_record_cannot_clear_degraded_auto_feed_block() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)
    service.record_strategy_performance(
        StrategyPerformanceRecord(
            strategy_id="strat_alpha",
            strategy_version="1.0",
            as_of=datetime(2026, 1, 5, tzinfo=timezone.utc),
            realized_max_drawdown=0.0,
            realized_total_return=0.0,
            observation_days=1,
            source="auto_feed",
            valuation_status="provider_error",
            data_mode=DataMode.fixture,
        )
    )
    service.record_strategy_performance(
        StrategyPerformanceRecord(
            strategy_id="strat_alpha",
            strategy_version="1.0",
            as_of=datetime(2026, 1, 6, tzinfo=timezone.utc),
            realized_max_drawdown=0.0,
            realized_total_return=0.0,
            observation_days=1,
            source="manual_override",
            valuation="daily_close",
        )
    )

    allowed, detail = service.strategy_activation_allowed(
        "strat_alpha", execution_level="level_3"
    )

    assert allowed is False
    assert detail == "strategy_performance_valuation_degraded"
    assert service.repositories.strategy_approval_tickets.require(ticket.ticket_id).status == (
        StrategyApprovalTicketStatus.approved
    )


def test_newer_manual_record_cannot_mask_ready_auto_mdd_breach() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)
    service.record_strategy_performance(
        _ready_auto_record(
            service,
            mdd=0.05,
            as_of=datetime(2026, 1, 5, tzinfo=timezone.utc),
        )
    )
    service.record_strategy_performance(
        StrategyPerformanceRecord(
            strategy_id="strat_alpha",
            strategy_version="1.0",
            as_of=datetime(2026, 1, 6, tzinfo=timezone.utc),
            realized_max_drawdown=0.0,
            realized_total_return=0.0,
            observation_days=1,
            source="manual_override",
        )
    )

    allowed, detail = service.strategy_activation_allowed(
        "strat_alpha",
        execution_level="level_3",
    )

    assert allowed is False
    assert detail == "no_active_strategy_approval"
    stored = service.repositories.strategy_approval_tickets.require(ticket.ticket_id)
    assert stored.status == StrategyApprovalTicketStatus.expired
    assert "mdd_exceeds_backtest_1_5x" in stored.reapproval_triggers


def test_fill_after_auto_refresh_invalidates_fill_watermark() -> None:
    service = HarnessService()
    ticket = _approved_ticket(service)
    _seed_attributed_fill(
        service,
        fill_id="fill-before-refresh",
        filled_at=datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc),
    )
    service.record_strategy_performance(_ready_auto_record(service))
    _seed_attributed_fill(
        service,
        fill_id="fill-after-refresh",
        filled_at=datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc),
    )

    allowed, detail = service.strategy_activation_allowed(
        "strat_alpha",
        execution_level="level_3",
    )

    assert allowed is False
    assert detail == "strategy_performance_fill_watermark_stale"
    assert service.repositories.strategy_approval_tickets.require(ticket.ticket_id).status == (
        StrategyApprovalTicketStatus.approved
    )


def test_inconsistent_auto_feed_normalization_blocks_activation() -> None:
    service = HarnessService()
    _approved_ticket(service)
    service.record_strategy_performance(
        StrategyPerformanceRecord(
            strategy_id="strat_alpha",
            strategy_version="1.0",
            realized_max_drawdown=0.0,
            realized_total_return=0.0,
            observation_days=1,
            source="auto_feed",
            valuation="daily_close",
            valuation_status="complete",
            normalization_basis="degraded_equity_epoch_changed",
            normalization_equity=10_000,
            data_mode=DataMode.fixture,
        )
    )

    allowed, detail = service.strategy_activation_allowed(
        "strat_alpha", execution_level="level_3"
    )

    assert allowed is False
    assert detail == "strategy_performance_normalization_degraded"


def test_auto_feed_data_mode_mismatch_blocks_activation() -> None:
    service = HarnessService()
    _approved_ticket(service)
    service.record_strategy_performance(
        _ready_auto_record(service).model_copy(
            update={"data_mode": DataMode.local_historical}
        )
    )

    allowed, detail = service.strategy_activation_allowed(
        "strat_alpha",
        execution_level="level_3",
    )

    assert allowed is False
    assert detail == "strategy_performance_data_mode_mismatch"


def test_calendar_holiday_config_change_invalidates_old_auto_record() -> None:
    original = HarnessService()
    _approved_ticket(original)
    original.record_strategy_performance(_ready_auto_record(original))
    changed = HarnessService(
        original.repositories,
        performance_calendar=SimpleKrxCalendar(
            holidays=(date(2026, 1, 5),)
        ),
    )

    allowed, detail = changed.strategy_activation_allowed(
        "strat_alpha",
        execution_level="level_3",
    )

    assert allowed is False
    assert detail == "strategy_performance_calendar_mismatch"


def test_market_data_revision_invalidates_old_auto_record() -> None:
    evaluated_at = datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc)
    original = HarnessService(
        market_data_provider=_CloseProvider(100.0),
        performance_clock=lambda: evaluated_at,
    )
    ticket = _approved_ticket(original)
    _seed_attributed_fill(
        original,
        fill_id="fill-market-data-watermark",
        filled_at=datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc),
    )
    record = original.compute_strategy_performance("strat_alpha", "1.0")
    assert record is not None
    assert record.valuation_status == "complete"
    original.record_strategy_performance(record)

    revised = HarnessService(
        original.repositories,
        market_data_provider=_CloseProvider(50.0),
        performance_clock=lambda: evaluated_at,
    )
    allowed, detail = revised.strategy_activation_allowed(
        "strat_alpha",
        execution_level="level_3",
    )

    assert allowed is False
    assert detail == "strategy_performance_market_data_mismatch"
    assert revised.repositories.strategy_approval_tickets.require(ticket.ticket_id).status == (
        StrategyApprovalTicketStatus.approved
    )


def test_market_data_unavailable_blocks_old_auto_record() -> None:
    evaluated_at = datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc)
    original = HarnessService(
        market_data_provider=_CloseProvider(100.0),
        performance_clock=lambda: evaluated_at,
    )
    _approved_ticket(original)
    _seed_attributed_fill(
        original,
        fill_id="fill-market-data-unavailable",
        filled_at=datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc),
    )
    record = original.compute_strategy_performance("strat_alpha", "1.0")
    assert record is not None
    original.record_strategy_performance(record)
    unavailable = HarnessService(
        original.repositories,
        market_data_provider=_UnavailableCloseProvider(),
        performance_clock=lambda: evaluated_at,
    )

    allowed, detail = unavailable.strategy_activation_allowed(
        "strat_alpha",
        execution_level="level_3",
    )

    assert allowed is False
    assert detail == "strategy_performance_market_data_unavailable"


def test_transient_provider_failure_cannot_bypass_mdd_expiry() -> None:
    evaluated_at = datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc)
    original = HarnessService(
        market_data_provider=_CloseProvider(100.0),
        performance_clock=lambda: evaluated_at,
    )
    ticket = _approved_ticket(original)
    _seed_attributed_fill(
        original,
        fill_id="fill-transient-provider",
        filled_at=datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc),
    )
    record = original.compute_strategy_performance("strat_alpha", "1.0")
    assert record is not None
    assert record.realized_max_drawdown > 0
    original.record_strategy_performance(record)
    fail_once = _FailOnceThenCloseProvider(100.0)
    retried = HarnessService(
        original.repositories,
        market_data_provider=fail_once,
        performance_clock=lambda: evaluated_at,
    )

    allowed, detail = retried.strategy_activation_allowed(
        "strat_alpha",
        execution_level="level_3",
    )

    assert allowed is False
    assert detail == "strategy_performance_market_data_unavailable"
    assert fail_once.calls == 1
    assert retried.repositories.strategy_approval_tickets.require(ticket.ticket_id).status == (
        StrategyApprovalTicketStatus.approved
    )


def test_complete_open_position_record_becomes_stale_at_next_session() -> None:
    evaluated_at = datetime(2026, 1, 8, 7, 0, tzinfo=timezone.utc)
    service = HarnessService(performance_clock=lambda: evaluated_at)
    _approved_ticket(service)
    service.record_strategy_performance(
        StrategyPerformanceRecord(
            strategy_id="strat_alpha",
            strategy_version="1.0",
            realized_max_drawdown=0.0,
            realized_total_return=0.0,
            observation_days=1,
            source="auto_feed",
            valuation="daily_close",
            valuation_status="complete",
            normalization_basis="first_order_account_equity",
            normalization_equity=10_000,
            normalization_snapshot_id="snap-stale-test",
            market_data_as_of_session=date(2026, 1, 7),
            data_mode=DataMode.fixture,
            has_open_positions=True,
            calendar_name="simple_krx",
            valuation_start_session=date(2026, 1, 5),
            calendar_as_of_session=date(2026, 1, 7),
            calendar_fingerprint=service._performance_calendar_fingerprint(
                date(2026, 1, 5),
                date(2026, 1, 7),
            ),
        )
    )

    allowed, detail = service.strategy_activation_allowed(
        "strat_alpha", execution_level="level_3"
    )

    assert allowed is False
    assert detail == "strategy_performance_stale"


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
