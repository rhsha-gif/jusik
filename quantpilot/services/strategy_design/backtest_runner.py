"""Code-run backtest for a designer-team recipe: full period, purged walk-forward, PSR/DSR/MinTRL.

The agents never run this; the job does, and the forensics agent reads the
resulting `BacktestReport` JSON. Trial counting for the deflated Sharpe comes
from an append-only ledger next to the recipes (`.trials.jsonl`): every
design run is one more trial on this data, whether or not it was kept.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, Field

from quantpilot.packages.core.backtest.engine import run_backtest
from quantpilot.packages.core.backtest.metrics import daily_returns
from quantpilot.packages.core.backtest.schemas import BacktestAssumptions, BacktestRequest, BacktestResult, BacktestSignal
from quantpilot.packages.core.backtest.statistics import (
    deflated_sharpe,
    min_track_record_length,
    sharpe_moments,
    trial_sharpe_variance,
)
from quantpilot.packages.core.backtest.validation import build_walk_forward_windows, trading_dates_from_price_history
from quantpilot.packages.core.schemas import StrategyRecipe
from quantpilot.services.strategy_design.rule_engine import generate_signals


class WalkForwardWindowReport(BaseModel):
    window_id: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_days: int = 0
    test_days: int = 0
    signals: int
    total_return: float | None = None
    max_drawdown: float | None = None
    simplified_sharpe: float | None = None
    sharpe_per_period: float | None = None
    filled_trades: int = 0


class BacktestReport(BaseModel):
    strategy_id: str
    recipe_version: str
    start_date: str | None = None
    end_date: str | None = None
    signals_generated: int
    metrics: dict[str, Any]
    walk_forward: list[WalkForwardWindowReport] = Field(default_factory=list)
    walk_forward_config: dict[str, int] = Field(default_factory=dict)
    statistics: dict[str, Any] = Field(default_factory=dict)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    data_provenance: dict[str, Any] = Field(default_factory=dict)
    trades: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    engine_limits: list[str] = Field(
        default_factory=lambda: [
            "KRX ±30% price limits, tick size and volatility interruption (VI) are not modelled",
            "fills: next_open_limit_touch; risk exits marketable at next open",
            "slippage is a research assumption, not broker-confirmed",
            "universe: local CSV, 15 symbols, ~2 years (fewer than two market cycles)",
        ]
    )
    research_only: bool = True


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def _per_period_sharpe(result: BacktestResult) -> float | None:
    return sharpe_moments(daily_returns([p.equity for p in result.equity_curve])).sharpe


def read_trials(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_trial(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def statistics_summary(
    result: BacktestResult,
    *,
    trials_so_far: int,
    trial_sharpes: Sequence[float] | None,
    window_sharpes: Sequence[float],
) -> dict[str, Any]:
    """Same convention as `jobs.run_local_backtest`: per-period Sharpe, population moments, n_trials = prior + 1."""

    moments = sharpe_moments(daily_returns([p.equity for p in result.equity_curve]))
    n_trials = trials_so_far + 1
    if trial_sharpes:
        variance_source = "trials_ledger"
        variance = trial_sharpe_variance(list(trial_sharpes))
    else:
        variance_source = "walk_forward_windows"
        variance = trial_sharpe_variance(list(window_sharpes))
    psr = dsr = expected_max = min_trl = None
    if moments.sharpe is not None and moments.skew is not None and moments.kurtosis is not None:
        deflated = deflated_sharpe(moments.sharpe, n=moments.n, skew=moments.skew, kurtosis=moments.kurtosis, n_trials=n_trials, trials_sr_variance=variance)
        psr, dsr, expected_max = deflated.psr, deflated.dsr, deflated.expected_max_sr
        min_trl = min_track_record_length(moments.sharpe, skew=moments.skew, kurtosis=moments.kurtosis, confidence=0.95)
    return {
        "sharpe_per_period": _round(moments.sharpe),
        "n": moments.n,
        "skew": _round(moments.skew),
        "kurtosis": _round(moments.kurtosis),
        "psr": _round(psr),
        "dsr": _round(dsr),
        "expected_max_sr": _round(expected_max),
        "n_trials": n_trials,
        "trials_sr_variance": _round(variance),
        "min_trl_95": _round(min_trl),
        "variance_source": variance_source,
    }


def run_recipe_backtest(
    recipe: StrategyRecipe,
    price_history: list[dict[str, Any]],
    market_data_provider: Any,
    *,
    assumptions: BacktestAssumptions,
    initial_cash: float = 10_000_000.0,
    max_position_weight: float = 0.15,
    trials_so_far: int = 0,
    trial_sharpes: Sequence[float] | None = None,
) -> BacktestReport:
    signals = generate_signals(recipe, price_history, max_position_weight=max_position_weight)
    full = run_backtest(
        BacktestRequest(strategy_id=recipe.strategy_id, recipe_version=recipe.version, signals=signals, initial_cash=initial_cash, assumptions=assumptions),
        market_data_provider,
    )
    wf = dict((recipe.validation or {}).get("walk_forward") or {})
    config = {
        "train_size": int(wf.get("train_size", 60)),
        "test_size": int(wf.get("test_size", 20)),
        "purge_bars": int(wf.get("purge_bars", 0)),
        "embargo_bars": int(wf.get("embargo_bars", 0)),
    }
    windows = build_walk_forward_windows(trading_dates_from_price_history(price_history), **config)
    reports: list[WalkForwardWindowReport] = []
    window_sharpes: list[float] = []
    for window in windows:
        subset: list[BacktestSignal] = [s for s in signals if window.test_start <= s.signal_date <= window.test_end]
        item = WalkForwardWindowReport(
            window_id=window.window_id,
            train_start=window.train_start.isoformat(),
            train_end=window.train_end.isoformat(),
            test_start=window.test_start.isoformat(),
            test_end=window.test_end.isoformat(),
            train_days=window.train_days,
            test_days=window.test_days,
            signals=len(subset),
        )
        if subset:
            result = run_backtest(
                BacktestRequest(
                    strategy_id=recipe.strategy_id,
                    recipe_version=recipe.version,
                    signals=subset,
                    initial_cash=initial_cash,
                    assumptions=assumptions,
                    start_date=window.test_start,
                    end_date=window.test_end,
                ),
                market_data_provider,
            )
            sharpe = _per_period_sharpe(result)
            item = item.model_copy(
                update={
                    "total_return": result.metrics.total_return,
                    "max_drawdown": result.metrics.max_drawdown,
                    "simplified_sharpe": result.metrics.simplified_sharpe,
                    "sharpe_per_period": _round(sharpe),
                    "filled_trades": result.metrics.filled_trades,
                }
            )
            if sharpe is not None:
                window_sharpes.append(sharpe)
        reports.append(item)

    filled = [t for t in full.trades if t.status == "filled"]
    blocked = [t for t in full.trades if t.status == "blocked"]
    summary = full.input_summary if isinstance(full.input_summary, dict) else {}
    provenance = dict(summary.get("data_provenance") or {})
    if not provenance:
        provenance = {"source": type(market_data_provider).__name__, "note": "provider exposes no provenance/quality hooks; local CSV research data"}
    return BacktestReport(
        strategy_id=recipe.strategy_id,
        recipe_version=recipe.version,
        start_date=full.start_date.isoformat() if full.start_date else None,
        end_date=full.end_date.isoformat() if full.end_date else None,
        signals_generated=len(signals),
        metrics=full.metrics.model_dump(mode="json"),
        walk_forward=reports,
        walk_forward_config=config,
        warnings=list(full.warnings) + ([f"walk-forward train spans are {config['train_size']} bars minus purge {config['purge_bars']} = {config['train_size'] - config['purge_bars']} effective bars"] if config["purge_bars"] else []),
        statistics=statistics_summary(full, trials_so_far=trials_so_far, trial_sharpes=trial_sharpes, window_sharpes=window_sharpes),
        assumptions=assumptions.model_dump(mode="json"),
        data_quality=dict(summary.get("data_quality") or {}),
        data_provenance=provenance,
        trades={
            "filled": len(filled),
            "blocked": len(blocked),
            "buys": sum(1 for t in filled if t.side == "buy"),
            "sells": sum(1 for t in filled if t.side == "sell"),
            "blocked_reasons": sorted({t.blocked_reason or "" for t in blocked} - {""}),
        },
    )


def trial_entry(recipe: StrategyRecipe, report: BacktestReport, hypothesis: str) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "strategy_id": recipe.strategy_id,
        "version": recipe.version,
        "hypothesis": hypothesis,
        "sharpe_per_period": report.statistics.get("sharpe_per_period"),
        "dsr": report.statistics.get("dsr"),
        "filled_trades": report.trades.get("filled"),
    }
