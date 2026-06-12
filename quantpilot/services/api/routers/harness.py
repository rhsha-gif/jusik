from __future__ import annotations

from fastapi import APIRouter, Depends

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.services.api.dependencies import get_harness_service


router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "live_trading_enabled": False, "default_broker": "mock"}


@router.post("/harness/run-smoke")
def run_smoke(service: HarnessService = Depends(get_harness_service)) -> dict[str, object]:
    return service.run_smoke()
