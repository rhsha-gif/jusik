from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from quantpilot.paper.intraday import replay as replay_module
from quantpilot.paper.intraday.replay import calibrate, replay
from quantpilot.paper.intraday.strategy import SPECS
from quantpilot.paper.strategy import Signal


UTC = timezone.utc
SPEC = SPECS[0]


@pytest.fixture(autouse=True)
def six_minute_fixture_windows(monkeypatch):
    # These tests isolate execution mechanics in a six-minute toy session.
    monkeypatch.setattr(replay_module, "ENTRY_CUTOFF_MINUTES", 0)
    monkeypatch.setattr(replay_module, "LIQUIDATION_MINUTES", 0)


@dataclass(frozen=True)
class _Session:
    open: datetime
    close: datetime


class _Calendar:
    def session(self, value):
        if isinstance(value, str):
            day = datetime.fromisoformat(value).replace(tzinfo=UTC)
        else:
            day = value.astimezone(UTC)
        if day.date().isoformat() != "2026-09-11":
            return None
        return _Session(
            datetime(2026, 9, 11, 9, tzinfo=UTC),
            datetime(2026, 9, 11, 9, 6, tzinfo=UTC),
        )


def _bar(minute, *, symbol="000001", close=100.0, high=101.0, low=99.0, volume=1000.0):
    start = datetime(2026, 9, 11, 9, minute, tzinfo=UTC)
    return {
        "symbol": symbol,
        "start": start.isoformat(),
        "available_at": (start + timedelta(minutes=1)).isoformat(),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "source": "fixture",
    }


def _dataset(bars, *, universes=None, events=None):
    value = {
        "schema_version": 1,
        "data_mode": "fixture",
        "bars": bars,
        "universes": (
            universes
            if universes is not None
            else [
                {
                    "at": "2026-09-11T09:00:00+00:00",
                    "source": "fixture_rank",
                    "symbols": sorted({bar["symbol"] for bar in bars}),
                    "metadata": {"ranking": "fixture"},
                }
            ]
        ),
        "events": events or [],
        "provenance": {"source": "unit_test"},
    }
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    value["sha256"] = hashlib.sha256(encoded).hexdigest()
    return value


def _one_signal(bars, now, session_open, spec, calibration=None):
    if bars and bars[-1].start.minute == 0:
        return [
            Signal(
                spec.strategy_id,
                bars[-1].symbol,
                100.0,
                95.0,
                105.0,
                0.5,
                "unit signal",
                spec.version,
                1.0,
            )
        ]
    return []


def _run(monkeypatch, bars, **kwargs):
    monkeypatch.setattr(replay_module, "evaluate", _one_signal)
    return replay(_dataset(bars), SPEC, calendar=_Calendar(), **kwargs)


def test_empty_dataset_is_json_safe_and_reports_missing_coverage():
    result = replay(
        _dataset([], universes=[]),
        SPEC,
        days=["2026-09-11"],
        calendar=_Calendar(),
    )

    json.dumps(result, allow_nan=False)
    assert result["trades"] == []
    assert result["daily_pnl"] == {"2026-09-11": 0.0}
    assert "missing_coverage:no_bars:2026-09-11" in result["quality_issues"]


def test_late_bar_is_not_backdated_and_utc_duplicate_is_rejected(monkeypatch):
    late = _bar(0)
    late["available_at"] = "2026-09-11T09:02:00Z"
    duplicate_a = _bar(1)
    duplicate_b = dict(duplicate_a)
    duplicate_b["start"] = "2026-09-11T18:01:00+09:00"
    duplicate_b["available_at"] = "2026-09-11T18:02:00+09:00"

    result = _run(monkeypatch, [late, duplicate_a, duplicate_b])

    assert result["trades"] == []
    assert any(
        "pit_invalid:duplicate_or_revision" in issue
        for issue in result["quality_issues"]
    )


def test_future_universe_is_not_used_retroactively(monkeypatch):
    universe = [
        {
            "at": "2026-09-11T09:04:00Z",
            "source": "future_rank",
            "symbols": ["000001"],
            "metadata": {"ranking": "fixture"},
        }
    ]
    monkeypatch.setattr(replay_module, "evaluate", _one_signal)

    result = replay(
        _dataset([_bar(i) for i in range(6)], universes=universe),
        SPEC,
        calendar=_Calendar(),
    )

    assert result["trades"] == []
    assert any(
        "pit_invalid:no_current_session_universe" in issue
        for issue in result["quality_issues"]
    )


def test_signal_never_fills_on_decision_bar_and_participation_is_partial(monkeypatch):
    result = _run(
        monkeypatch,
        [_bar(i, volume=1000) for i in range(6)],
        participation=0.01,
        fill_fraction=0.5,
    )

    assert result["round_trips"] == 1
    trade = result["trades"][0]
    assert trade["entry_at"] == "2026-09-11T09:02:00Z"
    assert trade["quantity"] == 5
    assert trade["reason"] == "session_close"


def test_stop_wins_when_stop_and_target_touch_same_minute(monkeypatch):
    bars = [_bar(0), _bar(1), _bar(2, high=106, low=94)] + [
        _bar(i) for i in range(3, 6)
    ]

    result = _run(monkeypatch, bars)

    assert result["round_trips"] == len(result["trades"]) == 1
    assert result["trades"][0]["reason"] == "stop"
    assert result["trades"][0]["exit_price"] == 95.0


def test_adverse_stop_gap_and_entry_price_gap_are_conservative(monkeypatch):
    stop_gap = [_bar(0), _bar(1), _bar(2, close=90, high=92, low=89)] + [
        _bar(i) for i in range(3, 6)
    ]
    stopped = _run(monkeypatch, stop_gap)
    assert stopped["trades"][0]["exit_price"] == 90.0

    entry_gap = [_bar(0), _bar(1, close=106, high=106)] + [_bar(i) for i in range(2, 6)]
    blocked = _run(monkeypatch, entry_gap)
    assert blocked["trades"] == []
    assert any(
        "execution_invalid:entry_price_gap" in issue
        for issue in blocked["quality_issues"]
    )


def test_missing_closing_bar_remains_unresolved(monkeypatch):
    result = _run(monkeypatch, [_bar(i) for i in range(5)])

    assert result["round_trips"] == 0
    assert result["unresolved"][0]["reason"] == "missing_session_close_bar"
    assert any(
        "missing_coverage:session_close" in issue for issue in result["quality_issues"]
    )


def test_gap_resets_symbol_warmup(monkeypatch):
    observed_lengths = []

    def needs_three_contiguous(bars, now, session_open, spec, calibration=None):
        if bars and bars[-1].start.minute == 3:
            observed_lengths.append(len(bars))
        return []

    monkeypatch.setattr(replay_module, "evaluate", needs_three_contiguous)
    result = replay(
        _dataset([_bar(0), _bar(2), _bar(3), _bar(4), _bar(5)]),
        SPEC,
        calendar=_Calendar(),
    )

    assert result["trades"] == []
    assert observed_lengths == [2]
    assert any(
        "missing_coverage:bar_gap" in issue for issue in result["quality_issues"]
    )


def test_trailing_stop_does_not_apply_to_the_already_elapsed_bar(monkeypatch):
    monkeypatch.setattr(replay_module, "evaluate", _one_signal)
    monkeypatch.setattr(
        replay_module,
        "protective_exit",
        lambda position, bars, now, spec: (99.5, None),
    )
    bars = [
        _bar(0),
        _bar(1),
        _bar(2),
        _bar(3, low=99.0),
        _bar(4, low=99.0),
        _bar(5),
    ]

    result = replay(_dataset(bars), SPEC, calendar=_Calendar())

    assert result["trades"][0]["reason"] == "stop"
    assert result["trades"][0]["exit_at"] == "2026-09-11T09:03:00Z"


def test_late_stop_update_cannot_use_an_earlier_intrabar_low(monkeypatch):
    monkeypatch.setattr(replay_module, "evaluate", _one_signal)
    monkeypatch.setattr(
        replay_module,
        "protective_exit",
        lambda p, bars, now, spec: (101 if now.minute >= 3 else 99.5, None),
    )
    bars = [
        _bar(0),
        _bar(1),
        _bar(2, close=102, high=103, low=100),
        _bar(3, close=102, high=103, low=100.5),
        _bar(4, close=100, high=102, low=99.5),
        _bar(5),
    ]
    for row in bars:
        row["available_at"] = (
            datetime.fromisoformat(row["available_at"]) + timedelta(seconds=5)
        ).isoformat()
    result = replay(_dataset(bars), SPEC, calendar=_Calendar())
    assert result["trades"][0]["exit_at"] == "2026-09-11T09:05:05Z"
    assert result["trades"][0]["net_pnl"] < 0
    assert any(
        "stop_update_overlaps_bar" in issue for issue in result["quality_issues"]
    )


def test_daily_loss_room_blocks_additional_risk(monkeypatch):
    symbols = ("000001", "000002", "000003")

    def recurring_signal(bars, now, session_open, spec, calibration=None):
        if bars and bars[-1].start.minute in {0, 3}:
            return [
                Signal(
                    spec.strategy_id,
                    bars[-1].symbol,
                    100.0,
                    95.0,
                    105.0,
                    0.5,
                    "risk cap signal",
                    spec.version,
                    1.0,
                )
            ]
        return []

    monkeypatch.setattr(replay_module, "evaluate", recurring_signal)
    bars = []
    for minute in range(6):
        for symbol in symbols:
            if minute == 2:
                bars.append(
                    _bar(
                        minute,
                        symbol=symbol,
                        close=95,
                        high=100,
                        low=94,
                        volume=1_000_000,
                    )
                )
            else:
                bars.append(_bar(minute, symbol=symbol, volume=1_000_000))

    result = replay(_dataset(bars), SPEC, calendar=_Calendar())

    assert result["round_trips"] >= 2
    assert result["daily_pnl"]["2026-09-11"] >= -50_000.0
    # Integer sizing can leave a small residual budget; it is not a quota of two trades per day.
    losses = [r for r in result["trades"] if r["net_pnl"] < 0]
    assert sum(-r["net_pnl"] for r in losses) <= 50_000


def test_additional_stop_gap_stress_degrades_net_pnl(monkeypatch):
    bars = [
        _bar(0),
        _bar(1),
        _bar(2, close=94, high=100, low=93),
        _bar(3),
        _bar(4),
        _bar(5),
    ]
    base = _run(monkeypatch, bars)
    stressed = _run(monkeypatch, bars, adverse_gap_bps=50)
    assert base["round_trips"] == stressed["round_trips"] == 1
    assert stressed["net_pnl"] < base["net_pnl"]
    assert stressed["assumptions"]["additional_stop_gap_bps"] == 50


def test_position_and_risk_caps_limit_quantity_and_names(monkeypatch):
    symbols = ("000001", "000002", "000003")
    bars = [
        _bar(minute, symbol=symbol, volume=1_000_000)
        for minute in range(6)
        for symbol in symbols
    ]
    monkeypatch.setattr(replay_module, "evaluate", _one_signal)

    result = replay(_dataset(bars), SPEC, calendar=_Calendar())

    assert result["round_trips"] == 2
    assert {trade["symbol"] for trade in result["trades"]} == {"000001", "000002"}
    assert all(trade["quantity"] <= 5000 for trade in result["trades"])


def test_higher_slippage_degrades_net_result_without_double_charging(monkeypatch):
    bars = [_bar(i) for i in range(6)]
    five = _run(monkeypatch, bars, slippage_bps=5)
    ten = _run(monkeypatch, bars, slippage_bps=10)

    assert ten["net_pnl"] < five["net_pnl"]
    assert ten["trades"][0]["entry_price"] == five["trades"][0]["entry_price"]
    assert ten["trades"][0]["exit_price"] == five["trades"][0]["exit_price"]


def test_calibration_uses_only_completed_training_episodes():
    result = calibrate(
        {
            "round_trips": 999,
            "trades": [
                {"r_multiple": 2.0},
                {"r_multiple": -1.0},
                {"r_multiple": 1.0},
                {"r_multiple": 0.0},
            ],
            "unresolved": [{"r_multiple": 100.0}],
        }
    )

    assert result == {
        "round_trips": 4,
        "p_win": 0.5,
        "mean_win_r": 1.5,
        "mean_loss_r": 0.5,
    }


@pytest.mark.parametrize("latency", [0, -1])
def test_noncausal_latency_is_rejected(latency):
    with pytest.raises(ValueError, match="latency_minutes"):
        replay(_dataset([]), SPEC, latency_minutes=latency, calendar=_Calendar())
