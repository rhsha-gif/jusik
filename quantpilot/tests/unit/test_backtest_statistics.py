from __future__ import annotations

from datetime import date, timedelta
from math import exp, sqrt
from pathlib import Path
from statistics import NormalDist, mean, pstdev

import pytest

from quantpilot.jobs.run_local_backtest import parse_args, run_local_backtest
from quantpilot.packages.core.backtest import (
    build_walk_forward_windows,
    deflated_sharpe,
    deflated_sharpe_from_trials,
    expected_max_sharpe,
    min_track_record_length,
    probabilistic_sharpe,
    sharpe_moments,
)
from quantpilot.tests.unit.test_run_local_backtest_job import _write_local_data

_NORMAL = NormalDist()
_EULER_GAMMA = 0.5772156649


# --- probabilistic Sharpe ------------------------------------------------------


def test_psr_is_one_half_at_zero_sharpe_for_any_n() -> None:
    for n in (2, 10, 1_000):
        assert probabilistic_sharpe(0.0, n=n, skew=0.0, kurtosis=3.0) == pytest.approx(0.5)


def test_psr_matches_hand_computed_normal_case() -> None:
    # skew=0, kurtosis=3 -> denominator sqrt(1 + sr^2 / 2) = sqrt(1.005)
    z = 0.1 * sqrt(100) / sqrt(1.005)
    assert z == pytest.approx(0.99751, abs=1e-5)
    psr = probabilistic_sharpe(0.1, n=101, skew=0.0, kurtosis=3.0)
    assert psr is not None
    assert psr == pytest.approx(0.84077, abs=1e-3)
    assert psr == pytest.approx(_NORMAL.cdf(z), abs=1e-12)


def test_psr_is_monotone_in_n_and_in_sharpe() -> None:
    by_n = [probabilistic_sharpe(0.1, n=n, skew=0.0, kurtosis=3.0) for n in (5, 20, 100, 500)]
    by_sr = [probabilistic_sharpe(sr, n=50, skew=0.0, kurtosis=3.0) for sr in (0.0, 0.05, 0.1, 0.3)]
    assert by_n == sorted(by_n) and len(set(by_n)) == len(by_n)
    assert by_sr == sorted(by_sr) and len(set(by_sr)) == len(by_sr)


def test_psr_returns_none_when_undefined() -> None:
    assert probabilistic_sharpe(0.1, n=1, skew=0.0, kurtosis=3.0) is None
    # a large positive skew with a large SR pushes the variance adjustment below zero
    assert probabilistic_sharpe(2.0, n=50, skew=3.0, kurtosis=1.0) is None


# --- expected max Sharpe / deflated Sharpe ------------------------------------


def test_expected_max_sharpe_is_zero_for_a_single_trial() -> None:
    assert expected_max_sharpe(1, 1.0) == 0.0
    assert expected_max_sharpe(0, 1.0) == 0.0
    assert expected_max_sharpe(10, 0.0) == 0.0


def test_expected_max_sharpe_matches_formula_for_ten_trials() -> None:
    n_trials = 10
    expected = (1 - _EULER_GAMMA) * _NORMAL.inv_cdf(1 - 1 / n_trials) + _EULER_GAMMA * _NORMAL.inv_cdf(
        1 - 1 / (n_trials * exp(1))
    )
    observed = expected_max_sharpe(n_trials, 1.0)
    assert observed == pytest.approx(expected, abs=1e-9)
    # Independent guard against a formula typo: Z^-1(0.9)=1.2816, Z^-1(1-1/(10e))=1.7893.
    assert observed == pytest.approx(1.5746, abs=0.01)
    # variance scales the result by sqrt(V)
    assert expected_max_sharpe(n_trials, 4.0) == pytest.approx(2 * observed, abs=1e-12)


def test_dsr_never_exceeds_psr_and_equals_it_for_one_trial() -> None:
    cases = [
        dict(sr=0.1, n=101, skew=0.0, kurtosis=3.0),
        dict(sr=0.05, n=30, skew=-0.5, kurtosis=4.0),
        dict(sr=0.3, n=250, skew=0.2, kurtosis=6.0),
    ]
    for case in cases:
        for n_trials, variance in ((1, 0.5), (2, 0.01), (10, 0.02), (100, 0.1)):
            result = deflated_sharpe(
                case["sr"],
                n=case["n"],
                skew=case["skew"],
                kurtosis=case["kurtosis"],
                n_trials=n_trials,
                trials_sr_variance=variance,
            )
            assert result.psr is not None and result.dsr is not None
            assert result.dsr <= result.psr + 1e-12
            assert result.n_trials == n_trials
            assert result.trials_sr_variance == variance
            if n_trials == 1:
                assert result.dsr == result.psr
                assert result.expected_max_sr == 0.0
            else:
                assert result.dsr < result.psr
                assert result.expected_max_sr > 0.0


def test_deflated_sharpe_from_trials_uses_sample_variance_of_the_list() -> None:
    trials = [0.02, 0.05, 0.11, -0.01]
    from_list = deflated_sharpe_from_trials(0.1, n=101, skew=0.0, kurtosis=3.0, trial_sharpes=trials)
    sample_variance = sum((t - mean(trials)) ** 2 for t in trials) / (len(trials) - 1)
    assert from_list.n_trials == 4
    assert from_list.trials_sr_variance == pytest.approx(sample_variance)
    assert from_list.expected_max_sr == pytest.approx(expected_max_sharpe(4, sample_variance))

    overridden = deflated_sharpe_from_trials(
        0.1, n=101, skew=0.0, kurtosis=3.0, trial_sharpes=trials, n_trials=5
    )
    assert overridden.n_trials == 5

    single = deflated_sharpe_from_trials(0.1, n=101, skew=0.0, kurtosis=3.0, trial_sharpes=[0.2])
    assert single.trials_sr_variance == 0.0
    assert single.expected_max_sr == 0.0
    assert single.dsr == single.psr


# --- minimum track record length ---------------------------------------------


def test_min_track_record_length_matches_hand_computed_value() -> None:
    observed = min_track_record_length(0.1, skew=0.0, kurtosis=3.0, confidence=0.95)
    assert observed is not None
    assert observed == pytest.approx(1 + 1.005 * (1.644854 / 0.1) ** 2, abs=0.5)
    assert observed == pytest.approx(272.9, abs=0.5)


def test_min_track_record_length_is_none_when_sharpe_does_not_beat_benchmark() -> None:
    assert min_track_record_length(0.0, skew=0.0, kurtosis=3.0) is None
    assert min_track_record_length(-0.1, skew=0.0, kurtosis=3.0) is None
    assert min_track_record_length(0.1, skew=0.0, kurtosis=3.0, benchmark_sr=0.1) is None
    assert min_track_record_length(0.1, skew=0.0, kurtosis=3.0, benchmark_sr=0.05) is not None
    with pytest.raises(ValueError):
        min_track_record_length(0.1, skew=0.0, kurtosis=3.0, confidence=1.0)


# --- Sharpe moments ------------------------------------------------------------


def test_sharpe_moments_match_population_definitions() -> None:
    series = [0.01, -0.02, 0.03, 0.0, 0.01]
    moments = sharpe_moments(series)

    mu = mean(series)
    sigma = pstdev(series)
    m2 = sum((x - mu) ** 2 for x in series) / len(series)
    m3 = sum((x - mu) ** 3 for x in series) / len(series)
    m4 = sum((x - mu) ** 4 for x in series) / len(series)

    assert moments.n == 5
    assert moments.mean == pytest.approx(0.006)
    assert moments.mean == pytest.approx(mu)
    assert moments.stdev == pytest.approx(sigma)
    assert moments.sharpe == pytest.approx(mu / sigma)
    assert moments.skew == pytest.approx(m3 / m2**1.5)
    assert moments.kurtosis == pytest.approx(m4 / m2**2)


def test_sharpe_moments_degrade_safely_on_short_or_flat_series() -> None:
    empty = sharpe_moments([])
    assert (empty.n, empty.sharpe, empty.skew, empty.kurtosis, empty.mean, empty.stdev) == (0, None, None, None, 0.0, 0.0)

    short = sharpe_moments([0.01, 0.02])
    assert short.n == 2 and short.sharpe is None and short.skew is None and short.kurtosis is None
    assert short.mean == pytest.approx(0.015)

    flat = sharpe_moments([0.01, 0.01, 0.01, 0.01])
    assert flat.n == 4 and flat.sharpe is None and flat.stdev == 0.0 and flat.mean == pytest.approx(0.01)

    normal_like = sharpe_moments([-0.02, -0.01, 0.0, 0.01, 0.02])
    assert normal_like.skew == pytest.approx(0.0)
    assert normal_like.kurtosis == pytest.approx(1.7)  # uniform-like, below the normal's 3


# --- walk-forward purge / embargo ----------------------------------------------


def _dates(count: int) -> list[date]:
    return [date(2026, 1, 1) + timedelta(days=index) for index in range(count)]


def test_walk_forward_defaults_reproduce_pre_purge_windows_exactly() -> None:
    dates = _dates(100)
    windows = build_walk_forward_windows(dates, train_size=60, test_size=20)

    # Pre-change expectation, rebuilt from the original slicing rule.
    expected = []
    start = 0
    while start + 60 + 20 <= 100:
        train = dates[start : start + 60]
        test = dates[start + 60 : start + 80]
        expected.append((f"wf_{len(expected) + 1:03d}", train[0], train[-1], test[0], test[-1], 60, 20))
        start += 20
    assert [
        (w.window_id, w.train_start, w.train_end, w.test_start, w.test_end, w.train_days, w.test_days)
        for w in windows
    ] == expected

    assert len(windows) == 2
    assert windows[0].train_start == dates[0]
    assert windows[0].train_end == dates[59]
    assert windows[0].test_start == dates[60]
    assert windows[0].test_end == dates[79]
    assert windows[1].train_start == dates[20]
    assert windows[1].test_end == dates[99]

    explicit_zero = build_walk_forward_windows(dates, train_size=60, test_size=20, purge_bars=0, embargo_bars=0)
    assert explicit_zero == windows


def test_walk_forward_purge_shrinks_train_end_without_moving_test() -> None:
    dates = _dates(100)
    windows = build_walk_forward_windows(dates, train_size=60, test_size=20, purge_bars=5)
    baseline = build_walk_forward_windows(dates, train_size=60, test_size=20)

    assert len(windows) == len(baseline) == 2
    first = windows[0]
    assert first.train_start == dates[0]
    assert first.train_end == dates[54]
    assert first.train_days == 55
    assert (first.test_start, first.test_end, first.test_days) == (dates[60], dates[79], 20)
    assert windows[1].train_start == dates[20]
    assert windows[1].train_end == dates[74]
    assert (windows[1].test_start, windows[1].test_end) == (baseline[1].test_start, baseline[1].test_end)


def test_walk_forward_embargo_delays_test_start_and_keeps_test_size() -> None:
    dates = _dates(100)
    windows = build_walk_forward_windows(dates, train_size=60, test_size=20, embargo_bars=3)

    first = windows[0]
    assert first.train_end == dates[59]
    assert first.train_days == 60
    assert first.test_start == dates[63]
    assert first.test_end == dates[82]
    assert first.test_days == 20
    # the second window would need dates[83..102]; only 100 dates exist
    assert len(windows) == 1

    combined = build_walk_forward_windows(dates, train_size=60, test_size=20, purge_bars=5, embargo_bars=3)
    assert combined[0].train_end == dates[54]
    assert combined[0].test_start == dates[63]


def test_walk_forward_rejects_invalid_purge_and_embargo() -> None:
    dates = _dates(100)
    with pytest.raises(ValueError):
        build_walk_forward_windows(dates, train_size=60, test_size=20, purge_bars=60)
    with pytest.raises(ValueError):
        build_walk_forward_windows(dates, train_size=60, test_size=20, purge_bars=-1)
    with pytest.raises(ValueError):
        build_walk_forward_windows(dates, train_size=60, test_size=20, embargo_bars=-1)
    with pytest.raises(ValueError):
        build_walk_forward_windows(dates, train_size=60, test_size=20, purge_bars=1.5)  # type: ignore[arg-type]


# --- run_local_backtest job ----------------------------------------------------

_STATISTICS_KEYS = {
    "sharpe_per_period",
    "n",
    "skew",
    "kurtosis",
    "psr",
    "dsr",
    "expected_max_sr",
    "n_trials",
    "trials_sr_variance",
    "min_trl_95",
    "variance_source",
}


def test_job_reports_statistics_with_trials_so_far(tmp_path: Path) -> None:
    _write_local_data(tmp_path, [100.0 + (offset % 5) for offset in range(90)])

    args = parse_args(
        [
            "--data-dir",
            str(tmp_path),
            "--train-size",
            "40",
            "--test-size",
            "20",
            "--trials-so-far",
            "4",
        ]
    )
    summary = run_local_backtest(args)

    statistics = summary["statistics"]
    assert set(statistics) == _STATISTICS_KEYS
    assert statistics["n_trials"] == 5
    assert statistics["n"] == 89
    assert statistics["variance_source"] == "walk_forward_windows"
    assert summary["walk_forward"]["purge_bars"] == 0
    assert summary["walk_forward"]["embargo_bars"] == 0
    for key in ("sharpe_per_period", "psr", "dsr", "expected_max_sr", "trials_sr_variance", "min_trl_95"):
        assert statistics[key] is None or isinstance(statistics[key], float)
    # the existing output keys stay in place
    assert summary["research_only"] is True
    assert summary["live_trading_approval"] is False


def test_job_uses_supplied_trial_sharpes_and_passes_purge_embargo(tmp_path: Path) -> None:
    _write_local_data(tmp_path, [100.0 + (offset % 5) for offset in range(90)])

    args = parse_args(
        [
            "--data-dir",
            str(tmp_path),
            "--train-size",
            "40",
            "--test-size",
            "20",
            "--purge-bars",
            "5",
            "--embargo-bars",
            "2",
            "--trials-so-far",
            "2",
            "--trial-sharpes",
            "0.05,0.10,-0.02",
        ]
    )
    assert args.trial_sharpes == [0.05, 0.10, -0.02]
    summary = run_local_backtest(args)

    statistics = summary["statistics"]
    assert statistics["n_trials"] == 3
    assert statistics["variance_source"] == "trial_sharpes"
    expected_variance = sum((t - mean(args.trial_sharpes)) ** 2 for t in args.trial_sharpes) / 2
    assert statistics["trials_sr_variance"] == pytest.approx(expected_variance, abs=1e-6)
    assert summary["walk_forward"]["purge_bars"] == 5
    assert summary["walk_forward"]["embargo_bars"] == 2

    # embargo of 2 bars: the first test window starts 2 sessions after train_size=40 bars
    first_window = summary["walk_forward"]["windows"][0]
    assert first_window["test_start"] == (date(2026, 1, 1) + timedelta(days=42)).isoformat()


def test_job_rejects_malformed_statistics_flags(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--data-dir", str(tmp_path), "--trials-so-far", "-1"])
    with pytest.raises(SystemExit):
        parse_args(["--data-dir", str(tmp_path), "--trial-sharpes", "0.1,abc"])
