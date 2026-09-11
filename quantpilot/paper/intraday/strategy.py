"""Three predeclared hypotheses. Only completed, contiguous intraday bars enter rules."""

from dataclasses import asdict, dataclass, replace
from datetime import timedelta
import math

from quantpilot.paper.strategy import (
    Bar,
    Signal,
    _atr,
    _ema,
    aggregate_five_minutes,
    validate_bars,
)
from quantpilot.paper.intraday.deployment import digest, VALIDATED_POLICY


@dataclass(frozen=True)
class Spec:
    strategy_id: str
    lookback: int
    stop_atr: float
    reward_r: float
    max_hold_minutes: int
    volume_ratio: float = 1.5

    @property
    def version(self):
        from quantpilot.paper.intraday.deployment import IMPLEMENTATION_HASH

        return (
            "intraday2:"
            + digest({"spec": asdict(self), "implementation": IMPLEMENTATION_HASH})[:16]
        )


# A six-member family, declared before any data is evaluated.
SPECS = tuple(
    Spec(name, lookback, stop, reward, hold)
    for name, lookback, stop, reward, hold in (
        ("opening_range_breakout", 6, 1.2, 3.0, 120),
        ("opening_range_breakout", 9, 1.5, 3.0, 120),
        ("trend_pullback", 12, 1.2, 2.5, 90),
        ("trend_pullback", 18, 1.5, 2.5, 90),
        ("range_reversion", 12, 1.2, 0.0, 60),
        ("range_reversion", 18, 1.5, 0.0, 60),
    )
)


def get_spec(version):
    spec = next((s for s in SPECS if s.version == version), None)
    if spec is None:
        raise ValueError("unknown_intraday_strategy_version")
    return spec


def costs(
    entry,
    exit_price,
    fee_bps=VALIDATED_POLICY["fee_bps"],
    tax_bps=VALIDATED_POLICY["sell_tax_bps"],
    slippage_bps=VALIDATED_POLICY["slippage_bps"],
):
    return (
        entry * (fee_bps + slippage_bps) / 10000
        + exit_price * (fee_bps + tax_bps + slippage_bps) / 10000
    )


def expectation(signal, calibration):
    """Training-only empirical win/loss R estimates, common across hypotheses."""
    if not calibration or calibration.get("round_trips", 0) < 30:
        return None
    values = [calibration.get(k) for k in ("p_win", "mean_win_r", "mean_loss_r")]
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        return None
    p, win, loss = values
    if not 0 <= p <= 1 or win < 0 or loss < 0:
        return None
    return p * win - (1 - p) * loss


def evaluate(bars, now, session_open, spec, calibration=None):
    ordered = validate_bars(bars, now)
    today = [b for b in ordered if b.start >= session_open]
    if not today or today[-1].start + timedelta(minutes=1) != now.replace(
        second=0, microsecond=0
    ):
        return []
    current = today[-1]
    previous = today[-2] if len(today) > 1 else None
    # Exclude the context candle containing the signal minute.
    five = [
        b
        for b in aggregate_five_minutes(today)
        if b.start + timedelta(minutes=5) <= current.start
    ]
    if previous is None or len(five) < max(16, spec.lookback + 2):
        return []
    atr = _atr(five)
    if atr is None:
        return []
    fast = _ema([b.close for b in five], spec.lookback)
    rising = fast[-1] > fast[-3]
    stop = current.close - spec.stop_atr * atr
    target = current.close + spec.reward_r * (current.close - stop)
    triggered = False
    if spec.strategy_id == "opening_range_breakout":
        ceiling = max(b.high for b in five[-spec.lookback :])
        mean_volume = sum(b.volume for b in today[-21:-1]) / 20
        triggered = (
            rising
            and previous.close <= ceiling < current.close
            and current.volume >= spec.volume_ratio * mean_volume > 0
        )
        reason = "completed 1m volume breakout above prior 5m range"
    elif spec.strategy_id == "trend_pullback":
        triggered = (
            rising
            and five[-1].close >= fast[-1]
            and previous.low <= fast[-1] < current.close
            and current.close > previous.high
        )
        reason = "rising 5m trend with completed 1m pullback recovery"
    elif spec.strategy_id == "range_reversion":
        volume = sum(b.volume for b in today[:-1])
        if not volume:
            return []
        reference = (
            sum((b.high + b.low + b.close) / 3 * b.volume for b in today[:-1]) / volume
        )
        triggered = (
            previous.close < reference - spec.stop_atr * atr
            and current.close > previous.high
            and current.close < reference
        )
        stop = min(b.low for b in today[-5:]) - 0.25 * atr
        target = reference
        reason = "intraday overreaction recovery toward prior-volume VWAP"
    else:
        raise ValueError("undeclared_strategy")
    if not triggered or not 0 < stop < current.close < target:
        return []
    if target - current.close - costs(current.close, target) <= 0:
        return []
    signal = Signal(
        spec.strategy_id,
        current.symbol,
        current.close,
        stop,
        target,
        0.5,
        reason,
        spec.version,
        atr,
    )
    expected = expectation(signal, calibration)
    if calibration is not None:
        if expected is None or expected <= 0:
            return []
        signal = replace(signal, score=expected / (1 + expected))
    return [signal]


def rank(signals, weights, occupied):
    """Scores are transformed training expected R; symbol is only a final tie breaker."""
    ordered = sorted(
        signals, key=lambda s: (-s.score, s.strategy_id, s.version, s.symbol)
    )
    chosen, used = [], set(occupied)
    for s in ordered:
        if weights.get(s.strategy_id, 0) > 0 and s.symbol not in used:
            chosen.append(s)
            used.add(s.symbol)
    return chosen


def protective_exit(position, bars, now, spec):
    """Return a monotonically tighter stop and an optional completed-bar exit."""
    if now >= position["opened"] + timedelta(minutes=spec.max_hold_minutes):
        return position["stop"], "time_limit"
    if spec.strategy_id == "range_reversion":
        return position["stop"], None
    five = [
        b
        for b in aggregate_five_minutes(validate_bars(bars, now))
        if b.start + timedelta(minutes=5) <= now
    ]
    if len(five) < max(16, spec.lookback):
        return position["stop"], None
    atr = _atr(five)
    eligible = [b for b in five if b.start >= position["opened"]]
    high = max([position["stop"], *(b.high for b in eligible)])
    stop = (
        max(position["stop"], high - spec.stop_atr * atr) if atr else position["stop"]
    )
    average = _ema([b.close for b in five], spec.lookback)[-1]
    return stop, "trend_failed" if five[-1].close < average else None
