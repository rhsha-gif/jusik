"""Sharpe-ratio inference statistics for the strategy-design pipeline.

Implements the Probabilistic Sharpe Ratio (PSR), the Deflated Sharpe Ratio
(DSR), the expected maximum Sharpe ratio under multiple testing, and the
minimum track record length (MinTRL) after Bailey & López de Prado.

Conventions (shared by every function in this module):

* ``sr`` is the **per-period** Sharpe ratio ``mean(returns) / stdev(returns)``
  of the raw return series. It is *not* annualized; ``n`` is the number of
  return observations in that series.
* Moments are **population** moments (``statistics.pstdev`` and the matching
  central-moment ratios), consistent with ``metrics.calculate_simplified_sharpe``.
  Skew is ``m3 / m2 ** 1.5`` and kurtosis is the **non-excess** ``m4 / m2 ** 2``
  (a normal series has kurtosis 3).
* ``trials_sr_variance`` is the *sample* variance (``statistics.variance``) of
  the per-trial Sharpe estimates, because the trial list is itself a sample.

Stdlib only by design: the backtest package must not grow numeric dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, sqrt
from statistics import NormalDist, mean, pstdev, variance
from typing import Sequence

EULER_MASCHERONI = 0.5772156649

_NORMAL = NormalDist()


@dataclass(frozen=True)
class SharpeMoments:
    """Per-period Sharpe ratio and the return moments PSR/DSR need.

    ``sharpe``, ``skew`` and ``kurtosis`` are ``None`` when they are undefined:
    fewer than three observations, or zero variance. ``mean`` and ``stdev``
    are always populated (``0.0`` for an empty series).
    """

    sharpe: float | None
    n: int
    skew: float | None
    kurtosis: float | None
    mean: float
    stdev: float


@dataclass(frozen=True)
class DeflatedSharpe:
    """DSR alongside the ingredients that produced it."""

    dsr: float | None
    expected_max_sr: float
    n_trials: int
    trials_sr_variance: float
    psr: float | None


def sharpe_moments(returns: Sequence[float]) -> SharpeMoments:
    """Compute the per-period Sharpe ratio, skew and non-excess kurtosis.

    Population moments are used throughout. With ``n < 3`` or a degenerate
    (zero-variance) series the ratio-type fields are ``None`` rather than a
    misleading zero.
    """
    values = [float(value) for value in returns]
    n = len(values)
    if n == 0:
        return SharpeMoments(sharpe=None, n=0, skew=None, kurtosis=None, mean=0.0, stdev=0.0)
    mu = mean(values)
    sigma = pstdev(values) if n >= 2 else 0.0
    if n < 3 or sigma <= 0.0:
        return SharpeMoments(sharpe=None, n=n, skew=None, kurtosis=None, mean=mu, stdev=sigma)
    m2 = sigma * sigma
    m3 = sum((value - mu) ** 3 for value in values) / n
    m4 = sum((value - mu) ** 4 for value in values) / n
    return SharpeMoments(
        sharpe=mu / sigma,
        n=n,
        skew=m3 / (m2**1.5),
        kurtosis=m4 / (m2**2),
        mean=mu,
        stdev=sigma,
    )


def _sr_variance_scale(sr: float, *, skew: float, kurtosis: float) -> float:
    """``1 - γ3·SR + ((γ4 - 1)/4)·SR²`` — the non-normality adjustment shared by PSR and MinTRL."""
    return 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr


def probabilistic_sharpe(
    sr: float,
    *,
    n: int,
    skew: float,
    kurtosis: float,
    benchmark_sr: float = 0.0,
) -> float | None:
    """PSR = Φ((SR − SR*)·sqrt(n − 1) / sqrt(1 − γ3·SR + ((γ4 − 1)/4)·SR²)).

    Returns ``None`` when ``n < 2`` or the variance adjustment is not positive
    (the estimator's standard error is then undefined).
    """
    if n < 2:
        return None
    scale = _sr_variance_scale(sr, skew=skew, kurtosis=kurtosis)
    if scale <= 0.0:
        return None
    z = (sr - benchmark_sr) * sqrt(n - 1) / sqrt(scale)
    return _NORMAL.cdf(z)


def expected_max_sharpe(n_trials: int, trials_sr_variance: float) -> float:
    """Expected maximum Sharpe ratio across ``n_trials`` independent trials.

    SR* = sqrt(V)·((1 − γ)·Z⁻¹(1 − 1/N) + γ·Z⁻¹(1 − 1/(N·e))) with γ the
    Euler–Mascheroni constant. Returns ``0.0`` for ``N ≤ 1`` or ``V ≤ 0``.
    """
    if n_trials <= 1 or trials_sr_variance <= 0.0:
        return 0.0
    gamma = EULER_MASCHERONI
    first = _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    second = _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * exp(1.0)))
    return sqrt(trials_sr_variance) * ((1.0 - gamma) * first + gamma * second)


def deflated_sharpe(
    sr: float,
    *,
    n: int,
    skew: float,
    kurtosis: float,
    n_trials: int,
    trials_sr_variance: float,
) -> DeflatedSharpe:
    """DSR = PSR evaluated against the expected maximum Sharpe of the trials.

    ``psr`` in the result is the plain PSR against a zero benchmark, so the
    caller can see how much multiple testing deflated the confidence.
    """
    expected_max = expected_max_sharpe(n_trials, trials_sr_variance)
    return DeflatedSharpe(
        dsr=probabilistic_sharpe(sr, n=n, skew=skew, kurtosis=kurtosis, benchmark_sr=expected_max),
        expected_max_sr=expected_max,
        n_trials=n_trials,
        trials_sr_variance=trials_sr_variance,
        psr=probabilistic_sharpe(sr, n=n, skew=skew, kurtosis=kurtosis, benchmark_sr=0.0),
    )


def trial_sharpe_variance(trial_sharpes: Sequence[float]) -> float:
    """Sample variance of per-trial Sharpe estimates; ``0.0`` with fewer than two trials."""
    values = [float(value) for value in trial_sharpes]
    if len(values) < 2:
        return 0.0
    return variance(values)


def deflated_sharpe_from_trials(
    sr: float,
    *,
    n: int,
    skew: float,
    kurtosis: float,
    trial_sharpes: Sequence[float],
    n_trials: int | None = None,
) -> DeflatedSharpe:
    """Convenience wrapper: derive ``trials_sr_variance`` from a Sharpe list.

    ``n_trials`` defaults to ``len(trial_sharpes)``; pass it explicitly when
    the list holds only the *prior* trials and the current strategy must be
    counted as one more. With fewer than two Sharpes the variance is ``0.0``,
    so SR* is ``0.0`` and ``dsr == psr``.
    """
    trials = n_trials if n_trials is not None else len(trial_sharpes)
    return deflated_sharpe(
        sr,
        n=n,
        skew=skew,
        kurtosis=kurtosis,
        n_trials=trials,
        trials_sr_variance=trial_sharpe_variance(trial_sharpes),
    )


def min_track_record_length(
    sr: float,
    *,
    skew: float,
    kurtosis: float,
    benchmark_sr: float = 0.0,
    confidence: float = 0.95,
) -> float | None:
    """MinTRL = 1 + (1 − γ3·SR + ((γ4 − 1)/4)·SR²)·(Z_α / (SR − SR*))².

    The number of per-period observations needed before PSR reaches
    ``confidence``. ``None`` when ``sr <= benchmark_sr`` (never reachable) or
    the variance adjustment is not positive.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be strictly between 0 and 1")
    if sr <= benchmark_sr:
        return None
    scale = _sr_variance_scale(sr, skew=skew, kurtosis=kurtosis)
    if scale <= 0.0:
        return None
    z_alpha = _NORMAL.inv_cdf(confidence)
    return 1.0 + scale * (z_alpha / (sr - benchmark_sr)) ** 2


__all__ = [
    "EULER_MASCHERONI",
    "DeflatedSharpe",
    "SharpeMoments",
    "deflated_sharpe",
    "deflated_sharpe_from_trials",
    "expected_max_sharpe",
    "min_track_record_length",
    "probabilistic_sharpe",
    "sharpe_moments",
    "trial_sharpe_variance",
]
