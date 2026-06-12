from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import OperationReport
from quantpilot.services.api.dependencies import get_harness_service, require_latest


router = APIRouter()


class DailyReportRequest(BaseModel):
    policy_id: str | None = None


@router.post("/reports/daily")
def daily_report(
    request: DailyReportRequest,
    service: HarnessService = Depends(get_harness_service),
) -> OperationReport:
    policy_id = request.policy_id or require_latest(
        service.repositories.policies.list(),
        resource="policy",
        next_step="POST /api/policies/parse",
    ).policy_id
    return service.create_daily_report(policy_id=policy_id)
