from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import PortfolioPlan
from quantpilot.services.api.dependencies import get_harness_service, require_latest


router = APIRouter()


class PortfolioPlanRequest(BaseModel):
    policy_id: str | None = None


@router.post("/portfolio/plan")
def create_portfolio_plan(
    request: PortfolioPlanRequest,
    service: HarnessService = Depends(get_harness_service),
) -> PortfolioPlan:
    if request.policy_id is None:
        policy_id = require_latest(
            service.repositories.policies.list(),
            resource="policy",
            next_step="POST /api/policies/parse",
        ).policy_id
    else:
        policy_id = request.policy_id
        service.repositories.policies.require(policy_id)
    if not service.repositories.signals.list():
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no signals exist in the current harness session",
                "next_step": "POST /api/signals/run",
            },
        )
    return service.create_portfolio_plan(policy_id=policy_id)
