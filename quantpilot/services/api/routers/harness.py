from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantpilot.packages.core.data.mode import DataModeConfigError, is_data_mode_safe, raw_data_mode, resolve_data_mode
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.services.api.dependencies import get_harness_service


router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    live_trading_enabled: bool
    default_broker: str
    data_mode: str
    data_mode_safe: bool
    data_mode_error: str | None = None


@router.get("/health")
def health() -> HealthResponse:
    try:
        mode = resolve_data_mode()
    except DataModeConfigError as exc:
        return HealthResponse(
            status="blocked",
            live_trading_enabled=False,
            default_broker="mock",
            data_mode=raw_data_mode(),
            data_mode_safe=False,
            data_mode_error=str(exc),
        )
    safe = is_data_mode_safe(mode)
    return HealthResponse(
        status="ok" if safe else "blocked",
        live_trading_enabled=False,
        default_broker="mock",
        data_mode=mode.value,
        data_mode_safe=safe,
        data_mode_error=None if safe else f"DATA_MODE {mode.value!r} is not safe for this harness",
    )


@router.post("/harness/run-smoke")
def run_smoke(service: HarnessService = Depends(get_harness_service)) -> dict[str, object]:
    return service.run_smoke()
