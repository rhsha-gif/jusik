from __future__ import annotations

from quantpilot.services.research_agents.analytics.base_rate import (
    above_sma,
    conditional_forward_returns,
    drawdown_from_high_at_most,
)


def _bars(closes: list[float]) -> list[dict[str, object]]:
    return [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "close": c, "volume": 1} for i, c in enumerate(closes)]


def test_condition_never_sees_the_future() -> None:
    closes = [100.0] * 30 + [110.0] * 30
    bars = _bars(closes)
    report_a = conditional_forward_returns(bars, above_sma(5), horizons=(5,))
    # change every close after index 40 wildly: hits at or before 40 must not change
    bars_b = _bars(closes[:41] + [1.0] * 19)
    report_b = conditional_forward_returns(bars_b, above_sma(5), horizons=(5,))
    hits_a = [i for i in range(41) if above_sma(5)[1](bars, i)]
    hits_b = [i for i in range(41) if above_sma(5)[1](bars_b, i)]
    assert hits_a == hits_b
    assert report_a.condition == report_b.condition == "close > SMA5"


def test_forward_returns_and_loss_probability_are_computed_from_bars() -> None:
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 120.0, 90.0, 95.0, 130.0, 80.0, 85.0, 140.0]
    bars = _bars(closes)
    report = conditional_forward_returns(bars, above_sma(1), horizons=(2,))
    # above_sma(1) is "close > close" → never true; zero hits, empty horizon stats
    assert report.condition_hits == 0
    assert report.horizons[0].windows == 0 and report.horizons[0].median_return_pct is None

    always = ("always", lambda b, i: True)
    report = conditional_forward_returns(bars, always, horizons=(2,))
    h = report.horizons[0]
    assert h.windows == len(bars) - 2
    assert h.non_overlapping == 4  # starts 0, 3, 6, 9 (each window spans start+1..start+2)
    assert 0.0 <= h.loss_probability <= 1.0
    assert h.median_max_drawdown_pct <= 0.0
    assert "not independent" in report.sample_note


def test_drawdown_condition_uses_prior_high_only() -> None:
    closes = [100.0, 120.0, 130.0, 100.0, 90.0, 140.0]
    bars = _bars(closes)
    name, cond = drawdown_from_high_at_most(-20.0, lookback=10)
    assert name.startswith("drawdown from 10-session high")
    assert cond(bars, 3) is True   # 100 vs high 130 → -23%
    assert cond(bars, 4) is True   # 90 vs 130 → -30.8%
    assert cond(bars, 5) is False  # 140 is the new high
    assert cond(bars, 0) is False  # nothing before it
