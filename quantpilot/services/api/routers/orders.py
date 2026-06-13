from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from quantpilot.packages.core.execution.state_machine import ApprovalRequired, InvalidOrderTransition, RiskCheckRequired
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import OrderPlan, OrderStatus
from quantpilot.services.api.dependencies import get_harness_service, require_latest


router = APIRouter()


class OrderPlanRequest(BaseModel):
    portfolio_plan_id: str | None = None


class RejectOrderRequest(BaseModel):
    reason: str = "user_rejected"


class ModifyOrderRequest(BaseModel):
    quantity: float
    limit_price: float | None = None


@router.post("/orders/plan")
def create_order_plans(
    request: OrderPlanRequest,
    service: HarnessService = Depends(get_harness_service),
) -> list[OrderPlan]:
    portfolio_plan_id = request.portfolio_plan_id or require_latest(
        service.repositories.portfolio_plans.list(),
        resource="portfolio plan",
        next_step="POST /api/portfolio/plan",
    ).plan_id
    return service.create_order_plans(portfolio_plan_id=portfolio_plan_id)


@router.post("/orders/generate-proposals")
def generate_order_proposals(
    request: OrderPlanRequest,
    service: HarnessService = Depends(get_harness_service),
) -> list[OrderPlan]:
    portfolio_plan_id = request.portfolio_plan_id or require_latest(
        service.repositories.portfolio_plans.list(),
        resource="portfolio plan",
        next_step="POST /api/portfolio/plan",
    ).plan_id
    return service.generate_order_proposals(portfolio_plan_id=portfolio_plan_id)


@router.get("/orders/proposed")
def proposed_orders(service: HarnessService = Depends(get_harness_service)) -> list[OrderPlan]:
    return [order for order in service.repositories.order_plans.list() if order.status == OrderStatus.proposed]


@router.post("/orders/{order_plan_id}/approve")
def approve_order(
    order_plan_id: str,
    service: HarnessService = Depends(get_harness_service),
) -> OrderPlan:
    try:
        return service.approve_order_plan(order_plan_id)
    except InvalidOrderTransition as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{order_plan_id}/reject")
def reject_order(
    order_plan_id: str,
    request: RejectOrderRequest,
    service: HarnessService = Depends(get_harness_service),
) -> OrderPlan:
    try:
        return service.reject_order_plan(order_plan_id, reason=request.reason)
    except InvalidOrderTransition as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{order_plan_id}/modify")
def modify_order(
    order_plan_id: str,
    request: ModifyOrderRequest,
    service: HarnessService = Depends(get_harness_service),
) -> OrderPlan:
    try:
        return service.modify_order_plan(order_plan_id, quantity=request.quantity, limit_price=request.limit_price)
    except (InvalidOrderTransition, RiskCheckRequired, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{order_plan_id}/submit")
def submit_order(
    order_plan_id: str,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    try:
        order_plan, broker_order, fills = service.submit_order_plan(order_plan_id)
        return {"order_plan": order_plan, "broker_order": broker_order, "fills": fills}
    except (ApprovalRequired, RiskCheckRequired, InvalidOrderTransition, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/orders/{order_plan_id}/status")
def order_status(
    order_plan_id: str,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, str]:
    order_plan = service.repositories.order_plans.require(order_plan_id)
    return {"order_plan_id": order_plan.order_plan_id, "status": order_plan.status.value}
