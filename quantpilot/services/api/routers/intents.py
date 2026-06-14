from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.policy.parser import DEFAULT_POLICY_TEXT
from quantpilot.services.api.dependencies import get_harness_service


router = APIRouter()


class IntentRunRequest(BaseModel):
    text: str = DEFAULT_POLICY_TEXT
    user_id: str = "fixture-user"
    create_order_proposals: bool = True


@router.post("/intent/run")
def run_intent_workflow(
    request: IntentRunRequest,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    return service.run_investment_intent(
        text=request.text,
        user_id=request.user_id,
        create_order_proposals=request.create_order_proposals,
    )
