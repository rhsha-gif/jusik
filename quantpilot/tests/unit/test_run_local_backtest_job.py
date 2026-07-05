from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from quantpilot.jobs.run_local_backtest import parse_args, run_local_backtest


def _write_local_data(base: Path, closes: list[float]) -> None:
    with (base / "securities.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "name", "market", "sector"])
        writer.writerow(["AAA", "Alpha Test", "KR_STOCK", "technology"])
    start = date(2026, 1, 1)
    with (base / "ohlcv.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "date", "open", "high", "low", "close", "volume"])
        for offset, close in enumerate(closes):
            session = start + timedelta(days=offset)
            writer.writerow(
                ["AAA", session.isoformat(), close, close * 1.01, close * 0.99, close, 10_000]
            )


def test_job_runs_research_only_backtest_over_local_data(tmp_path: Path) -> None:
    _write_local_data(tmp_path, [100.0 + (offset % 5) for offset in range(90)])

    args = parse_args(["--data-dir", str(tmp_path), "--train-size", "40", "--test-size", "20"])
    summary = run_local_backtest(args)

    assert summary["research_only"] is True
    assert summary["live_trading_approval"] is False
    assert summary["bars"] == 90
    assert summary["trading_days"] == 90
    assert summary["walk_forward"]["windows"], "expected at least one walk-forward window"
    assert summary["acceptance"] == {
        "evaluated": False,
        "note": "no thresholds supplied; acceptance criteria are a pending human input",
    }
    assert summary["full_period"]["metrics"]["final_cash"] >= 0


def test_job_evaluates_acceptance_when_thresholds_are_supplied(tmp_path: Path) -> None:
    _write_local_data(tmp_path, [100.0 + (offset % 5) for offset in range(60)])

    args = parse_args(
        ["--data-dir", str(tmp_path), "--min-filled-trades", "1", "--train-size", "30", "--test-size", "15"]
    )
    summary = run_local_backtest(args)

    acceptance = summary["acceptance"]
    assert acceptance["research_only"] is True
    assert acceptance["live_trading_approval"] is False
    assert any(check["name"] == "min_filled_trades" for check in acceptance["checks"])
