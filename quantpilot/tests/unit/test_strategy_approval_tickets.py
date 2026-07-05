from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from quantpilot.packages.core.backtest import BacktestRequest, BacktestSignal, run_backtest
from quantpilot.packages.core.backtest.schemas import BacktestResult
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import (
    SignalAction,
    StrategyApprovalTicketStatus,
    utc_now,
)
from quantpilot.services.api.dependencies import get_harness_service
from quantpilot.services.api.main import app


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


def _recorded_evidence(
    service: HarnessService, *, strategy_id: str = "strat_alpha", version: str = "1.0"
) -> BacktestResult:
    result = run_backtest(
        BacktestRequest(
            strategy_id=strategy_id,
            recipe_version=version,
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
    return service.record_backtest_result(result)


def _create_ticket(service: HarnessService, evidence: BacktestResult, **overrides: Any):
    fields: dict[str, Any] = {
        "strategy_id": evidence.strategy_id,
        "strategy_version": evidence.recipe_version,
        "spec_hash": "hash_1",
        "backtest_report_id": evidence.result_id,
        "requested_execution_level": "level_3",
        "capital_budget_pct": 0.2,
    }
    fields.update(overrides)
    return service.create_strategy_approval_ticket(**fields)


def test_ticket_creation_fails_closed_without_backtest_evidence() -> None:
    service = HarnessService()

    with pytest.raises(RuntimeError, match="missing backtest evidence"):
        service.create_strategy_approval_ticket(
            strategy_id="strat_alpha",
            strategy_version="1.0",
            spec_hash="hash_1",
            backtest_report_id="bt_does_not_exist",
        )


def test_ticket_creation_fails_closed_on_strategy_mismatch() -> None:
    service = HarnessService()
    evidence = _recorded_evidence(service, strategy_id="strat_other")

    with pytest.raises(RuntimeError, match="does not match"):
        _create_ticket(service, evidence, strategy_id="strat_alpha")


def test_approved_ticket_gates_activation_by_level() -> None:
    service = HarnessService()
    evidence = _recorded_evidence(service)
    ticket = _create_ticket(service, evidence)

    assert [t.ticket_id for t in service.pending_strategy_tickets()] == [ticket.ticket_id]
    assert service.strategy_activation_allowed("strat_alpha", execution_level="level_3")[0] is False

    approved = service.approve_strategy_ticket(ticket.ticket_id, approved_by="tester")

    assert approved.status == StrategyApprovalTicketStatus.approved
    allowed, detail = service.strategy_activation_allowed("strat_alpha", execution_level="level_3")
    assert allowed is True
    assert detail == ticket.ticket_id
    # A level_3 approval never grants level_4 authority.
    assert service.strategy_activation_allowed("strat_alpha", execution_level="level_4")[0] is False


def test_level4_ticket_covers_level3_execution() -> None:
    service = HarnessService()
    evidence = _recorded_evidence(service)
    ticket = _create_ticket(service, evidence, requested_execution_level="level_4")
    service.approve_strategy_ticket(ticket.ticket_id)

    assert service.strategy_activation_allowed("strat_alpha", execution_level="level_4")[0] is True
    assert service.strategy_activation_allowed("strat_alpha", execution_level="level_3")[0] is True


def test_expired_ticket_fails_closed() -> None:
    service = HarnessService()
    evidence = _recorded_evidence(service)
    ticket = _create_ticket(service, evidence)
    service.approve_strategy_ticket(ticket.ticket_id)

    ticket.valid_until = utc_now() - timedelta(seconds=1)
    service.repositories.strategy_approval_tickets.update(ticket)

    assert service.strategy_activation_allowed("strat_alpha", execution_level="level_3")[0] is False
    stored = service.repositories.strategy_approval_tickets.require(ticket.ticket_id)
    assert stored.status == StrategyApprovalTicketStatus.expired


def test_new_ticket_supersedes_active_ticket_for_same_strategy() -> None:
    service = HarnessService()
    evidence = _recorded_evidence(service)
    first = _create_ticket(service, evidence)
    service.approve_strategy_ticket(first.ticket_id)

    second = _create_ticket(service, evidence, capital_budget_pct=0.3)

    stored_first = service.repositories.strategy_approval_tickets.require(first.ticket_id)
    assert stored_first.status == StrategyApprovalTicketStatus.superseded
    assert stored_first.superseded_by == second.ticket_id
    # The replacement is not yet approved, so activation stays closed.
    assert service.strategy_activation_allowed("strat_alpha", execution_level="level_3")[0] is False


def test_revoked_ticket_closes_activation() -> None:
    service = HarnessService()
    evidence = _recorded_evidence(service)
    ticket = _create_ticket(service, evidence)
    service.approve_strategy_ticket(ticket.ticket_id)
    assert service.strategy_activation_allowed("strat_alpha", execution_level="level_3")[0] is True

    revoked = service.revoke_strategy_ticket(ticket.ticket_id, reason="kill_switch_engaged")

    assert revoked.status == StrategyApprovalTicketStatus.revoked
    assert service.strategy_activation_allowed("strat_alpha", execution_level="level_3")[0] is False


def test_strategy_ticket_api_round_trip() -> None:
    service = HarnessService()
    evidence = _recorded_evidence(service)
    app.dependency_overrides[get_harness_service] = lambda: service
    try:
        client = TestClient(app)

        missing = client.post(
            "/api/execution/strategy-tickets/create",
            json={
                "strategy_id": "strat_alpha",
                "strategy_version": "1.0",
                "spec_hash": "hash_1",
                "backtest_report_id": "bt_missing",
            },
        )
        assert missing.status_code == 400

        created = client.post(
            "/api/execution/strategy-tickets/create",
            json={
                "strategy_id": "strat_alpha",
                "strategy_version": "1.0",
                "spec_hash": "hash_1",
                "backtest_report_id": evidence.result_id,
            },
        )
        assert created.status_code == 200
        ticket_id = created.json()["ticket_id"]

        pending = client.get("/api/execution/strategy-tickets/pending")
        assert [item["ticket_id"] for item in pending.json()] == [ticket_id]

        approved = client.post(
            f"/api/execution/strategy-tickets/{ticket_id}/approve", json={"approved_by": "tester"}
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        gate = client.get(
            "/api/execution/strategy-tickets/activation-allowed",
            params={"strategy_id": "strat_alpha", "execution_level": "level_3"},
        )
        assert gate.json()["allowed"] is True
        assert approved.json()["live_trading_enabled"] is False
    finally:
        app.dependency_overrides.clear()
