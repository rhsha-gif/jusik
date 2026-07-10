"""Opt-in KIS paper one-shot check; never runs in automated test suites."""

from __future__ import annotations

import os

import pytest

from quantpilot.jobs.run_kis_paper_session import run_from_environment


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_KIS_MANUAL_INTEGRATION") != "1",
    reason=(
        "manual KIS paper operator check; set RUN_KIS_MANUAL_INTEGRATION=1 "
        "and every explicit paper-session safety gate"
    ),
)


def test_manual_kis_paper_operator_one_shot() -> None:
    assert os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "false"
    assert os.environ.get("MARKET_ORDERS_ENABLED", "false").lower() == "false"

    result = run_from_environment()

    assert result.status in {"completed", "blocked"}
    assert result.reason_code != "paper_session_internal_failure"
