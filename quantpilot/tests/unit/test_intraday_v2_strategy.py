from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quantpilot.paper.intraday.strategy import (
    SPECS,
    evaluate,
    rank,
    expectation,
    protective_exit,
)
from quantpilot.paper.strategy import Bar, Signal


def test_declared_family_and_common_r_ranking():
    assert len(SPECS) == 6 and len({s.strategy_id for s in SPECS}) == 3
    assert len({s.version for s in SPECS}) == 6
    low = Signal("trend_pullback", "000001", 100, 95, 110, 0.2, "fixture")
    high = replace(low, symbol="999999", score=0.8)
    assert rank([low, high], {"trend_pullback": 0.5}, set()) == [high, low]
    assert expectation(low, {"round_trips": 29}) is None
    assert expectation(
        low, {"round_trips": 100, "p_win": 0.6, "mean_win_r": 2, "mean_loss_r": 1}
    ) == pytest.approx(0.8)


def test_future_completed_context_rejected_and_time_exit():
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    bars = [
        Bar("005930", start + timedelta(minutes=i), 100, 101, 99, 100, 100)
        for i in range(100)
    ]
    now = start + timedelta(minutes=100)
    assert evaluate(bars, now, start, SPECS[0]) == []
    with pytest.raises(ValueError):
        evaluate(bars, now - timedelta(minutes=1), start, SPECS[0])
    stop, reason = protective_exit({"stop": 90, "opened": start}, bars, now, SPECS[-1])
    assert (stop, reason) == (90, "time_limit")


def test_protection_ignores_incomplete_five_minute_groups():
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    bars = [
        Bar("005930", start + timedelta(minutes=i), 100, 101, 99, 100, 100)
        for i in range(100)
    ]
    position = {"stop": 90, "opened": start + timedelta(minutes=95)}
    before = protective_exit(position, bars, start + timedelta(minutes=100), SPECS[2])
    partial = [
        Bar("005930", start + timedelta(minutes=i), 100, 1000, 1, 2, 100)
        for i in range(100, 104)
    ]
    assert (
        protective_exit(
            position, bars + partial, start + timedelta(minutes=104), SPECS[2]
        )
        == before
    )


@pytest.mark.parametrize("spec_index", [0, 2, 4])
def test_each_hypothesis_emits_a_cost_positive_completed_minute_signal(spec_index):
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    bars = [
        Bar(
            "005930",
            start + timedelta(minutes=i),
            10000 + 10 * i,
            10040 + 10 * i,
            9960 + 10 * i,
            10000 + 10 * i,
            100,
        )
        for i in range(100)
    ]
    if spec_index == 0:
        bars[-1] = Bar("005930", bars[-1].start, 10990, 11220, 10960, 11200, 200)
    elif spec_index == 2:
        bars[-2] = Bar("005930", bars[-2].start, 10900, 10920, 10600, 10900, 100)
        bars[-1] = Bar("005930", bars[-1].start, 10900, 11020, 10890, 11000, 100)
    else:
        bars = [
            Bar("005930", start + timedelta(minutes=i), 10000, 10100, 9900, 10000, 100)
            for i in range(100)
        ]
        bars[-2] = Bar("005930", bars[-2].start, 9600, 9700, 9500, 9600, 100)
        bars[-1] = Bar("005930", bars[-1].start, 9700, 9850, 9650, 9800, 100)
    calibration = {"round_trips": 100, "p_win": 0.6, "mean_win_r": 2, "mean_loss_r": 1}
    signals = evaluate(
        bars, start + timedelta(minutes=100), start, SPECS[spec_index], calibration
    )
    assert len(signals) == 1
    assert signals[0].version == SPECS[spec_index].version
    assert signals[0].score == pytest.approx(0.8 / 1.8)
