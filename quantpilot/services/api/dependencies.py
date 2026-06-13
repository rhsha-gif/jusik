from __future__ import annotations

from collections.abc import Sequence
import os
from typing import TypeVar

from fastapi import HTTPException

from quantpilot.packages.core.data.mode import DataModeConfigError
from quantpilot.packages.core.data.providers import ProviderError
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.operator.service import OperatorService
from quantpilot.packages.db.repositories import RepositoryRegistry


repositories = RepositoryRegistry()
_harness_service: HarnessService | None = None
_operator_service: OperatorService | None = None
_service_config_key: tuple[str | None, ...] | None = None
T = TypeVar("T")


def _current_service_config_key() -> tuple[str | None, ...]:
    return (
        os.environ.get("DATA_MODE"),
        os.environ.get("LOCAL_DATA_DIR"),
        os.environ.get("EXTERNAL_HISTORICAL_PROVIDER"),
        os.environ.get("EXTERNAL_HISTORICAL_MARKET"),
        os.environ.get("EXTERNAL_HISTORICAL_SYMBOLS"),
        os.environ.get("EXTERNAL_HISTORICAL_START"),
        os.environ.get("EXTERNAL_HISTORICAL_END"),
        os.environ.get("EXTERNAL_HISTORICAL_HOLIDAYS"),
        os.environ.get("EXTERNAL_HISTORICAL_ADJUSTED"),
        os.environ.get("KIS_BASE_URL"),
    )


def _configuration_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "harness data mode configuration invalid",
            "message": str(exc),
            "data_mode": os.environ.get("DATA_MODE", "fixture"),
        },
    )


def _ensure_services() -> HarnessService:
    global _harness_service, _operator_service, _service_config_key

    config_key = _current_service_config_key()
    if _harness_service is None or config_key != _service_config_key:
        try:
            _harness_service = HarnessService.from_environment(repositories)
        except (DataModeConfigError, ProviderError) as exc:
            raise _configuration_error(exc)
        _operator_service = OperatorService(_harness_service)
        _service_config_key = config_key
    return _harness_service


def get_harness_service() -> HarnessService:
    return _ensure_services()


def get_operator_service() -> OperatorService:
    _ensure_services()
    if _operator_service is None:
        raise HTTPException(status_code=503, detail={"error": "operator service is not configured"})
    return _operator_service


def require_latest(items: Sequence[T], *, resource: str, next_step: str) -> T:
    if not items:
        raise HTTPException(
            status_code=409,
            detail={
                "error": f"no {resource} exists in the current harness session",
                "next_step": next_step,
            },
        )
    return items[-1]
