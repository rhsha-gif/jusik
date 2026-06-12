from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.policy.parser import DEFAULT_POLICY_TEXT, parse_policy_text
from quantpilot.packages.core.schemas import UserPolicy
from quantpilot.services.api.dependencies import get_harness_service


router = APIRouter()


class ParsePolicyRequest(BaseModel):
    text: str = DEFAULT_POLICY_TEXT
    user_id: str = "fixture-user"


class ConfirmPolicyRequest(BaseModel):
    policy_id: str


@router.post("/policies/preview")
def preview_policy(request: ParsePolicyRequest) -> dict[str, object]:
    policy = parse_policy_text(request.text, user_id=request.user_id)
    return {
        "confirmed": False,
        "policy": policy,
        "policy_json": policy.model_dump(mode="json"),
    }


@router.post("/policies/parse")
def parse_policy(
    request: ParsePolicyRequest,
    service: HarnessService = Depends(get_harness_service),
) -> UserPolicy:
    return service.parse_policy(request.text, user_id=request.user_id)


@router.post("/policies/confirm")
def confirm_policy(
    request: ConfirmPolicyRequest,
    service: HarnessService = Depends(get_harness_service),
) -> UserPolicy:
    return service.confirm_policy(request.policy_id)
