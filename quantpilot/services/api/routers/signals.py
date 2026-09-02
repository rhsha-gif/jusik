from __future__ import annotations

from fastapi import APIRouter, Depends

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import Signal
from quantpilot.services.api.dependencies import get_harness_service, require_operator_actor


router = APIRouter()


@router.post("/signals/run", dependencies=[Depends(require_operator_actor)])
def run_signals(service: HarnessService = Depends(get_harness_service)) -> list[Signal]:
    return service.run_signals()
