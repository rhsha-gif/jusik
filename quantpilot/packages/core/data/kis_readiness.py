from __future__ import annotations

import os
from datetime import datetime
from typing import Literal

from pydantic import Field

from quantpilot.packages.core.schemas import DataMode, HarnessModel, new_id, utc_now


class KisReadinessCheck(HarnessModel):
    name: str
    passed: bool
    detail: str


class KisReadinessReport(HarnessModel):
    report_id: str = Field(default_factory=lambda: new_id("kisready"))
    status: Literal["ready", "blocked"]
    data_mode: Literal["external_historical"] = DataMode.external_historical.value
    checks: list[KisReadinessCheck]
    blocking_reasons: list[str] = Field(default_factory=list)
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False
    generated_at: datetime = Field(default_factory=utc_now)


def _has_env(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def evaluate_kis_readiness() -> KisReadinessReport:
    required = ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCESS_TOKEN")
    checks = [
        KisReadinessCheck(
            name=name,
            passed=_has_env(name),
            detail="configured" if _has_env(name) else "missing",
        )
        for name in required
    ]
    base_url = os.environ.get("KIS_BASE_URL", "").strip()
    checks.append(
        KisReadinessCheck(
            name="KIS_BASE_URL",
            passed=bool(base_url),
            detail="configured" if base_url else "missing; historical client requires an explicit base URL",
        )
    )
    blocking = [check.name for check in checks if not check.passed]
    return KisReadinessReport(
        status="blocked" if blocking else "ready",
        checks=checks,
        blocking_reasons=blocking,
        live_trading_enabled=False,
        order_submission_enabled=False,
    )
