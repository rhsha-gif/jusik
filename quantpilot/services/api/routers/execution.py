from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from quantpilot.packages.core.execution.state_machine import ApprovalRequired, InvalidOrderTransition, RiskCheckRequired
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import TradeApprovalTicket
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
