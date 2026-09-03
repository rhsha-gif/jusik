from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantpilot.packages.core.data.mode import DataModeConfigError, is_data_mode_safe, raw_data_mode, resolve_data_mode
from quantpilot.packages.core.execution.state_machine import (
    fully_automated_operator_flag_enabled,
    live_trading_flag_enabled,
)
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.risk.gatekeeper import market_orders_enabled
from quantpilot.packages.db.repositories import RepositoryRegistry
from quantpilot.services.api.dependencies import get_harness_service, require_operator_actor


router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    live_trading_enabled: bool
    market_orders_enabled: bool
    guarded_autopilot_enabled: bool
    fully_automated_operator_enabled: bool
    default_broker: str
    data_mode: str
    data_mode_safe: bool
    data_mode_error: str | None = None


@router.get("/health")
def health() -> HealthResponse:
    live_trading = live_trading_flag_enabled()
    market_orders = market_orders_enabled()
    guarded_autopilot = os.getenv("GUARDED_AUTOPILOT_ENABLED", "false").lower() == "true"
    fully_automated_operator = fully_automated_operator_flag_enabled()
    broker_mode = os.getenv("BROKER_MODE", "mock")
    safety_adapter_safe = (
        not live_trading
        and not market_orders
        and not guarded_autopilot
        and not fully_automated_operator
        and broker_mode == "mock"
    )
    try:
        mode = resolve_data_mode()
    except DataModeConfigError as exc:
        return HealthResponse(
            status="blocked",
            live_trading_enabled=live_trading,
            market_orders_enabled=market_orders,
            guarded_autopilot_enabled=guarded_autopilot,
            fully_automated_operator_enabled=fully_automated_operator,
            default_broker=broker_mode,
            data_mode=raw_data_mode(),
            data_mode_safe=False,
            data_mode_error=str(exc),
        )
    safe = is_data_mode_safe(mode)
    return HealthResponse(
        status="ok" if safe and safety_adapter_safe else "blocked",
        live_trading_enabled=live_trading,
        market_orders_enabled=market_orders,
        guarded_autopilot_enabled=guarded_autopilot,
        fully_automated_operator_enabled=fully_automated_operator,
        default_broker=broker_mode,
        data_mode=mode.value,
        data_mode_safe=safe,
        data_mode_error=None if safe else f"DATA_MODE {mode.value!r} is not safe for this harness",
    )


@router.post("/harness/run-smoke", dependencies=[Depends(require_operator_actor)])
def run_smoke(service: HarnessService = Depends(get_harness_service)) -> dict[str, object]:
    isolated_service = HarnessService(
        RepositoryRegistry(),
        security_provider=service.security_provider,
        market_data_provider=service.market_data_provider,
        data_mode=service.data_mode,
    )
    return isolated_service.run_smoke()
