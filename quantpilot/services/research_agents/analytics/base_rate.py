"""Conditional forward-return base rates from daily bars.

`invest-judge` Step 3: compute the base rate from price history instead of
argument, condition it on a state resembling today's, never leak the future
into the condition, and state the sample limit next to the number. Pure
python, no pandas, deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Callable, Sequence

Bar = dict[str, Any]  # {"date": "YYYY-MM-DD", "close": float, "volume": int}
Condition = Callable[[Sequence[Bar], int], bool]  # (bars, index) -> True when the state holds at index

DEFAULT_HORIZONS = (60, 120, 250)


@dataclass(frozen=True)
class HorizonStats:
    horizon: int
    windows: int
    non_overlapping: int
    median_return_pct: float | None
    loss_probability: float | None
    median_max_drawdown_pct: float | None


@dataclass(frozen=True)
class BaseRateReport:
    condition: str
    sessions: int
    condition_hits: int
    horizons: list[HorizonStats] = field(default_factory=list)
    sample_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "sessions": self.sessions,
            "condition_hits": self.condition_hits,
            "horizons": [h.__dict__ for h in self.horizons],
            "sample_note": self.sample_note,
        }


def above_sma(window: int) -> tuple[str, Condition]:
    """Close at index is above the simple moving average of the *previous* `window` closes (inclusive of index)."""

    def _cond(bars: Sequence[Bar], index: int) -> bool:
        if index + 1 < window:
            return False
        closes = [float(b["close"]) for b in bars[index + 1 - window : index + 1]]
        return float(bars[index]["close"]) > sum(closes) / window

    return f"close > SMA{window}", _cond


def drawdown_from_high_at_most(pct: float, lookback: int = 250) -> tuple[str, Condition]:
    """Close at index is at least `pct` percent below the highest close of the previous `lookback` sessions."""

    def _cond(bars: Sequence[Bar], index: int) -> bool:
        if index < 1:
            return False
        window = bars[max(0, index - lookback) : index + 1]
        high = max(float(b["close"]) for b in window)
        return high > 0 and (float(bars[index]["close"]) / high - 1.0) * 100.0 <= pct

    return f"drawdown from {lookback}-session high <= {pct}%", _cond


def _forward(bars: Sequence[Bar], start: int, horizon: int) -> tuple[float, float]:
    entry = float(bars[start]["close"])
    path = [float(b["close"]) for b in bars[start + 1 : start + 1 + horizon]]
    ret = (path[-1] / entry - 1.0) * 100.0
    peak = entry
    worst = 0.0
    for close in path:
        peak = max(peak, close)
        worst = min(worst, (close / peak - 1.0) * 100.0)
    return round(ret, 2), round(worst, 2)


def conditional_forward_returns(
    bars: Sequence[Bar],
    condition: tuple[str, Condition],
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> BaseRateReport:
    """Only data up to and including the start index decides whether a start qualifies (no look-ahead)."""

    name, cond = condition
    hits = [i for i in range(len(bars)) if cond(bars, i)]
    stats: list[HorizonStats] = []
    for horizon in horizons:
        starts = [i for i in hits if i + horizon < len(bars)]
        if not starts:
            stats.append(HorizonStats(horizon, 0, 0, None, None, None))
            continue
        results = [_forward(bars, i, horizon) for i in starts]
        returns = [r for r, _ in results]
        drawdowns = [d for _, d in results]
        non_overlap = 0
        last_end = -1
        for i in starts:
            if i > last_end:
                non_overlap += 1
                last_end = i + horizon
        stats.append(
            HorizonStats(
                horizon=horizon,
                windows=len(starts),
                non_overlapping=non_overlap,
                median_return_pct=round(median(returns), 2),
                loss_probability=round(sum(1 for r in returns if r < 0) / len(returns), 3),
                median_max_drawdown_pct=round(median(drawdowns), 2),
            )
        )
    smallest = min((h.non_overlapping for h in stats), default=0)
    note = (
        f"{len(bars)} sessions; overlapping windows are not independent samples — "
        f"the smallest non-overlapping count across horizons is {smallest}. "
        "Treat these as reference base rates, not confidence intervals."
    )
    return BaseRateReport(condition=name, sessions=len(bars), condition_hits=len(hits), horizons=stats, sample_note=note)
