from __future__ import annotations

from fastapi import APIRouter, Depends

from quantpilot.packages.core.execution.safety_flags import (
    fully_automated_operator_flag_enabled,
    live_trading_flag_enabled,
    operator_kill_switch_engaged,
)
from quantpilot.packages.core.operator.reporting import render_operator_report_text
from quantpilot.packages.core.operator.schemas import OperatorRunRequest, OperatorRunResult
from quantpilot.packages.core.operator.service import OperatorService
from quantpilot.services.api.dependencies import get_operator_service


router = APIRouter()


@router.post("/operator/run-once")
def operator_run_once(
    request: OperatorRunRequest,
    service: OperatorService = Depends(get_operator_service),
) -> OperatorRunResult:
    return service.run_once(request)


@router.get("/operator/status")
def operator_status(service: OperatorService = Depends(get_operator_service)) -> dict[str, object]:
    return {
        "live_trading_enabled": False,
        "feature_flags": {
            "LIVE_TRADING_ENABLED": live_trading_flag_enabled(),
            "FULLY_AUTOMATED_OPERATOR_ENABLED": fully_automated_operator_flag_enabled(),
            "OPERATOR_KILL_SWITCH": operator_kill_switch_engaged(),
        },
        "registry": [
            {
                "strategy_id": entry.strategy_id,
                "version": entry.version,
                "status": entry.status,
                "allowed_execution_levels": entry.allowed_execution_levels,
                "disabled_reason": entry.disabled_reason,
            }
            for entry in service.registry.entries()
        ],
        "runs": len(service.reports),
    }


@router.get("/operator/reports/latest")
def latest_operator_report(service: OperatorService = Depends(get_operator_service)) -> dict[str, object]:
    if not service.reports:
        return {"report": None, "text": "no operator runs recorded"}
    report = service.reports[-1]
    return {"report": report.model_dump(mode="json"), "text": render_operator_report_text(report)}
