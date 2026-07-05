from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from quantpilot.packages.core.execution.state_machine import ApprovalRequired, InvalidOrderTransition, RiskCheckRequired
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import (
    OperatorNotification,
    StrategyApprovalTicket,
    StrategyDraft,
    StrategyPerformanceRecord,
    TradeApprovalTicket,
)
from quantpilot.packages.db.repositories import RepositoryError
from quantpilot.services.api.dependencies import get_harness_service, require_latest


router = APIRouter()


class ApprovalTicketGenerateRequest(BaseModel):
    policy_id: str | None = None
    portfolio_plan_id: str | None = None
    data_mode: Literal["fixture", "paper_trading", "live_trading_candidate"] = "live_trading_candidate"
    partial_allow: bool = False


class ApprovalTicketDecisionRequest(BaseModel):
    approved_by: str = "user"
    reason: str = "user_rejected"


def _policy_id(request_policy_id: str | None, service: HarnessService) -> str:
    return request_policy_id or require_latest(
        service.repositories.policies.list(),
        resource="policy",
        next_step="POST /api/policies/parse",
    ).policy_id


@router.post("/execution/approval-tickets/generate")
def generate_approval_tickets(
    request: ApprovalTicketGenerateRequest,
    service: HarnessService = Depends(get_harness_service),
) -> list[TradeApprovalTicket]:
    try:
        return service.generate_approval_tickets(
            policy_id=_policy_id(request.policy_id, service),
            portfolio_plan_id=request.portfolio_plan_id,
            data_mode=request.data_mode,
            partial_allow=request.partial_allow,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/execution/approval-tickets/pending")
def pending_approval_tickets(
    service: HarnessService = Depends(get_harness_service),
) -> list[TradeApprovalTicket]:
    return service.pending_approval_tickets()


@router.post("/execution/approval-tickets/{ticket_id}/approve-and-submit")
def approve_and_submit_approval_ticket(
    ticket_id: str,
    request: ApprovalTicketDecisionRequest,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    try:
        return service.approve_and_submit_approval_ticket(ticket_id, approved_by=request.approved_by)
    except (ApprovalRequired, InvalidOrderTransition, RiskCheckRequired, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/execution/approval-tickets/{ticket_id}/reject")
def reject_approval_ticket(
    ticket_id: str,
    request: ApprovalTicketDecisionRequest,
    service: HarnessService = Depends(get_harness_service),
) -> TradeApprovalTicket:
    try:
        return service.reject_approval_ticket(ticket_id, reason=request.reason)
    except (InvalidOrderTransition, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class StrategyTicketCreateRequest(BaseModel):
    strategy_id: str
    strategy_version: str
    spec_hash: str
    backtest_report_id: str
    requested_execution_level: Literal["level_3", "level_4"] = "level_3"
    capital_budget_pct: float = 0.2
    valid_days: int = 30
    reapproval_triggers: list[str] = []


class StrategyTicketDecisionRequest(BaseModel):
    approved_by: str = "user"
    reason: str = "user_rejected"


@router.post("/execution/strategy-tickets/create")
def create_strategy_ticket(
    request: StrategyTicketCreateRequest,
    service: HarnessService = Depends(get_harness_service),
) -> StrategyApprovalTicket:
    try:
        return service.create_strategy_approval_ticket(
            strategy_id=request.strategy_id,
            strategy_version=request.strategy_version,
            spec_hash=request.spec_hash,
            backtest_report_id=request.backtest_report_id,
            requested_execution_level=request.requested_execution_level,
            capital_budget_pct=request.capital_budget_pct,
            valid_days=request.valid_days,
            reapproval_triggers=request.reapproval_triggers,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/execution/strategy-tickets/pending")
def pending_strategy_tickets(
    service: HarnessService = Depends(get_harness_service),
) -> list[StrategyApprovalTicket]:
    return service.pending_strategy_tickets()


@router.post("/execution/strategy-tickets/{ticket_id}/approve")
def approve_strategy_ticket(
    ticket_id: str,
    request: StrategyTicketDecisionRequest,
    service: HarnessService = Depends(get_harness_service),
) -> StrategyApprovalTicket:
    try:
        return service.approve_strategy_ticket(ticket_id, approved_by=request.approved_by)
    except (RepositoryError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/execution/strategy-tickets/{ticket_id}/reject")
def reject_strategy_ticket(
    ticket_id: str,
    request: StrategyTicketDecisionRequest,
    service: HarnessService = Depends(get_harness_service),
) -> StrategyApprovalTicket:
    try:
        return service.reject_strategy_ticket(ticket_id, reason=request.reason)
    except (RepositoryError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/execution/strategy-tickets/{ticket_id}/revoke")
def revoke_strategy_ticket(
    ticket_id: str,
    request: StrategyTicketDecisionRequest,
    service: HarnessService = Depends(get_harness_service),
) -> StrategyApprovalTicket:
    try:
        return service.revoke_strategy_ticket(ticket_id, reason=request.reason)
    except (RepositoryError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class StrategyPerformanceRequest(BaseModel):
    strategy_id: str
    strategy_version: str
    realized_max_drawdown: float
    realized_total_return: float
    observation_days: int
    source: str = "manual"


@router.post("/execution/strategy-performance")
def record_strategy_performance(
    request: StrategyPerformanceRequest,
    service: HarnessService = Depends(get_harness_service),
) -> StrategyPerformanceRecord:
    try:
        return service.record_strategy_performance(
            StrategyPerformanceRecord(
                strategy_id=request.strategy_id,
                strategy_version=request.strategy_version,
                realized_max_drawdown=request.realized_max_drawdown,
                realized_total_return=request.realized_total_return,
                observation_days=request.observation_days,
                source=request.source,
            )
        )
    except (RepositoryError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/execution/strategy-performance/refresh")
def refresh_strategy_performance(
    service: HarnessService = Depends(get_harness_service),
) -> list[StrategyPerformanceRecord]:
    """Recompute realized performance from attributed fills (auto feed)."""
    return service.run_strategy_performance_feed()


@router.get("/notifications")
def list_notifications(
    unacknowledged_only: bool = False,
    service: HarnessService = Depends(get_harness_service),
) -> list[OperatorNotification]:
    return service.list_notifications(unacknowledged_only=unacknowledged_only)


@router.post("/notifications/{notification_id}/acknowledge")
def acknowledge_notification(
    notification_id: str,
    service: HarnessService = Depends(get_harness_service),
) -> OperatorNotification:
    try:
        return service.acknowledge_notification(notification_id)
    except (RepositoryError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class StrategyDraftRequest(BaseModel):
    symbols: list[str] = []
    sectors: list[str] = []
    note: str = ""


@router.post("/strategy-studio/draft")
def create_strategy_draft(
    request: StrategyDraftRequest,
    service: HarnessService = Depends(get_harness_service),
) -> StrategyDraft:
    try:
        return service.create_strategy_draft(
            symbols=request.symbols, sectors=request.sectors, note=request.note
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/strategy-studio/drafts/{draft_id}")
def get_strategy_draft(
    draft_id: str,
    service: HarnessService = Depends(get_harness_service),
) -> StrategyDraft:
    try:
        return service.repositories.strategy_drafts.require(draft_id)
    except (RepositoryError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/strategy-studio/drafts/{draft_id}/validate")
def validate_strategy_draft(
    draft_id: str,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    try:
        return service.validate_strategy_draft(draft_id)
    except (RepositoryError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/execution/strategy-tickets/activation-allowed")
def strategy_activation_allowed(
    strategy_id: str,
    execution_level: Literal["level_3", "level_4"] = "level_3",
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    allowed, detail = service.strategy_activation_allowed(
        strategy_id, execution_level=execution_level
    )
    return {"strategy_id": strategy_id, "execution_level": execution_level, "allowed": allowed, "detail": detail}
