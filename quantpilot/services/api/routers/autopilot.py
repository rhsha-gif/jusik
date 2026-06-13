from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.services.api.dependencies import get_harness_service, require_latest


router = APIRouter()


class AutopilotPolicyRequest(BaseModel):
    policy_id: str | None = None


class KillSwitchRequest(BaseModel):
    policy_id: str | None = None
    reason: str = "user_requested"


class ReleaseKillSwitchRequest(BaseModel):
    policy_id: str | None = None
    confirmation: str


def _policy_id(request_policy_id: str | None, service: HarnessService) -> str:
    return request_policy_id or require_latest(
        service.repositories.policies.list(),
        resource="policy",
        next_step="POST /api/policies/parse",
    ).policy_id


@router.post("/autopilot/guarded/run-once")
def guarded_run_once(
    request: AutopilotPolicyRequest,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    return service.run_guarded_autopilot_once(policy_id=_policy_id(request.policy_id, service))


@router.post("/autopilot/guarded/pause")
def pause_guarded(
    request: AutopilotPolicyRequest,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    return service.pause_guarded_autopilot(policy_id=_policy_id(request.policy_id, service))


@router.post("/autopilot/guarded/resume")
def resume_guarded(
    request: AutopilotPolicyRequest,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    return service.resume_guarded_autopilot(policy_id=_policy_id(request.policy_id, service))


@router.post("/autopilot/kill-switch")
def kill_switch(
    request: KillSwitchRequest,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    return service.engage_kill_switch(policy_id=_policy_id(request.policy_id, service), reason=request.reason)


@router.post("/autopilot/kill-switch/release")
def release_kill_switch(
    request: ReleaseKillSwitchRequest,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    try:
        return service.release_kill_switch(policy_id=_policy_id(request.policy_id, service), confirmation=request.confirmation)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/autopilot/status")
def autopilot_status(service: HarnessService = Depends(get_harness_service)) -> dict[str, object]:
    return service.autopilot_status()
