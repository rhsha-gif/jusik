from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.policy.parser import DEFAULT_POLICY_TEXT
from quantpilot.packages.core.universe.builder import build_candidate_universe
from quantpilot.services.api.dependencies import get_harness_service


router = APIRouter()


class Level12Request(BaseModel):
    policy_id: str | None = None
    text: str = DEFAULT_POLICY_TEXT
    user_id: str = "fixture-user"


def _policy_id_for_request(request: Level12Request, service: HarnessService) -> str:
    if request.policy_id is not None:
        return request.policy_id
    existing = service.repositories.policies.list()
    if existing:
        return existing[-1].policy_id
    return service.parse_policy(request.text, user_id=request.user_id).policy_id


@router.post("/level-1-2/run")
def run_level_1_2(
    request: Level12Request,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    return service.run_level_1_2(policy_id=_policy_id_for_request(request, service))


@router.post("/research/universe")
def research_universe(
    request: Level12Request,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    policy = service.repositories.policies.require(_policy_id_for_request(request, service))
    return {"policy_id": policy.policy_id, "candidates": build_candidate_universe(policy)}


@router.post("/research/analyst")
def analyst_reports(
    request: Level12Request,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    result = service.run_level_1_2_result(policy_id=_policy_id_for_request(request, service))
    return {"policy_id": result.policy.policy_id, "analyst_reports": result.analyst_reports}


@router.post("/signals/board")
def signal_board(
    request: Level12Request,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    result = service.run_level_1_2_result(policy_id=_policy_id_for_request(request, service))
    return {"policy_id": result.policy.policy_id, "signals": result.signals}


@router.post("/portfolio/rebalance-suggestions")
def rebalance_suggestions(
    request: Level12Request,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    result = service.run_level_1_2_result(policy_id=_policy_id_for_request(request, service))
    return {"policy_id": result.policy.policy_id, "rebalance": result.rebalance}


@router.post("/reports/research-signal-daily")
def research_signal_daily_report(
    request: Level12Request,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    result = service.run_level_1_2_result(policy_id=_policy_id_for_request(request, service))
    return {"policy_id": result.policy.policy_id, "daily_report": result.daily_report}
