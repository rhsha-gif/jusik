from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from quantpilot.packages.core.backtest import BacktestAssumptions, BacktestRequest, BacktestSignal, run_backtest
from quantpilot.packages.core.schemas import SignalAction
from quantpilot.packages.core.validation import (
    build_benchmark_relative_attribution,
    build_promotion_evidence_report,
    run_slippage_sensitivity,
    run_walk_forward_validation,
)


def _rows(days: int = 10) -> list[dict[str, Any]]:
    start = date(2026, 3, 1)
    rows: list[dict[str, Any]] = []
    for index in range(days):
        session = start + timedelta(days=index)
        close = 100.0 + index * 1.5
        rows.append(
            {
                "symbol": "AAA",
                "date": session.isoformat(),
                "open": close,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": 100_000,
            }
        )
    return rows


def _benchmark(days: int = 10) -> list[dict[str, Any]]:
    start = date(2026, 3, 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "close": 100.0 + index,
        }
        for index in range(days)
    ]


def _request() -> BacktestRequest:
    return BacktestRequest(
        strategy_id="evidence_strategy",
        recipe_version="1.0",
        initial_cash=10_000,
        assumptions=BacktestAssumptions(
            fee_bps=1.0,
            slippage_bps=5.0,
            sell_tax_bps=1.0,
            min_trading_days=1,
            min_filled_trades=1,
        ),
        signals=[
            BacktestSignal(
                symbol="AAA",
                signal_date=date(2026, 3, 5),
                action=SignalAction.buy_ready,
                target_weight_hint=0.5,
            )
        ],
    )


def test_promotion_evidence_report_is_conservative_and_serializable() -> None:
    request = _request()
    rows = _rows()
    validation_runs = run_walk_forward_validation(
        request,
        rows,
        train_size=4,
        test_size=2,
        step_size=2,
        data_mode="fixture",
    )
    slippage = run_slippage_sensitivity(
        request,
        rows,
        slippage_bps_values=[0.0, 5.0, 25.0],
        data_mode="fixture",
    )
    attribution = build_benchmark_relative_attribution(
        run_backtest(request, rows),
        _benchmark(),
    )

    report = build_promotion_evidence_report(
        validation_runs=validation_runs,
        slippage_sensitivity=slippage,
        benchmark_attribution=attribution,
        data_mode="fixture",
    )
    repeated = build_promotion_evidence_report(
        validation_runs=validation_runs,
        slippage_sensitivity=slippage,
        benchmark_attribution=attribution,
        data_mode="fixture",
    )
    payload = json.loads(report.model_dump_json())

    assert report.report_id == repeated.report_id
    assert payload["strategy_id"] == "evidence_strategy"
    assert payload["promotion_allowed"] is False
    assert payload["human_review_required"] is True
    assert payload["research_only"] is True
    assert payload["live_trading_approval"] is False
    assert payload["survivorship_status"]["status"] == "not_configured"
    assert payload["corporate_action_status"]["status"] == "not_configured"
    assert payload["pbo"]["status"] == "placeholder"
    assert payload["dsr"]["status"] == "placeholder"
    assert "deterministic_validation_cannot_promote" in payload["blockers"]
    assert "human_review_required" in payload["blockers"]
