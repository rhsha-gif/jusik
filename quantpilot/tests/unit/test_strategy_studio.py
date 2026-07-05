from __future__ import annotations

import pytest

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import StrategyDraftStatus


def test_draft_requires_symbols_or_sectors() -> None:
    service = HarnessService()

    with pytest.raises(RuntimeError, match="at least one symbol or sector"):
        service.create_strategy_draft()


def test_draft_fails_closed_when_nothing_matches() -> None:
    service = HarnessService()

    with pytest.raises(RuntimeError, match="no universe symbols match"):
        service.create_strategy_draft(sectors=["nonexistent-sector"])


def test_draft_to_ticket_full_vision_path() -> None:
    """Studio draft -> validate -> ticket -> approve -> activation gate opens."""
    service = HarnessService()

    draft = service.create_strategy_draft(sectors=["technology"], note="user picked tech")
    assert draft.status == StrategyDraftStatus.drafted
    assert draft.universe_symbols
    assert "arming principle" in draft.rationale

    validation = service.validate_strategy_draft(draft.draft_id)
    validated = validation["draft"]
    assert validated.status == StrategyDraftStatus.validated  # type: ignore[union-attr]
    assert validation["ticket_ready"] is True
    report_id = validation["backtest_report_id"]
    assert service.repositories.backtest_results.get(report_id) is not None  # type: ignore[arg-type]

    ticket = service.create_strategy_approval_ticket(
        strategy_id=draft.strategy_id,
        strategy_version=draft.strategy_version,
        spec_hash=draft.spec_hash,
        backtest_report_id=str(report_id),
        requested_execution_level="level_3",
        capital_budget_pct=0.2,
    )
    service.approve_strategy_ticket(ticket.ticket_id, approved_by="tester")

    allowed, detail = service.strategy_activation_allowed(
        draft.strategy_id, execution_level="level_3"
    )
    assert allowed is True
    assert detail == ticket.ticket_id


def test_validation_result_is_research_only_evidence() -> None:
    service = HarnessService()
    draft = service.create_strategy_draft(sectors=["technology"])

    validation = service.validate_strategy_draft(draft.draft_id)

    stored = service.repositories.backtest_results.require(str(validation["backtest_report_id"]))
    assert stored.research_only is True
    assert stored.live_trading_approval is False
    assert stored.assumptions.fee_bps == pytest.approx(1.40527)
    assert stored.assumptions.sell_tax_bps == 20.0
