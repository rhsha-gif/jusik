from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import inspect

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.schemas import SignalAction
from quantpilot.packages.core.signals.pullback_trend import (
    PullbackBar,
    PullbackSignalInput,
    PullbackTrendParameters,
    build_pullback_indicators,
    evaluate_pullback_signal,
)
import quantpilot.packages.core.signals.pullback_trend as pullback_module


def _bars(*, future_spike: bool = False, final_close: float | None = None) -> list[PullbackBar]:
    start = date(2026, 1, 1)
    bars: list[PullbackBar] = []
    closes = [100.0 + index * 0.20 for index in range(120)]
    closes.extend(closes[-1] - offset for offset in range(1, 15))
    closes.append(closes[-1] + 8.0)
    if final_close is not None:
        closes[-1] = final_close
    for index, close in enumerate(closes):
        bars.append(
            PullbackBar(
                symbol="AAA",
                session_date=start + timedelta(days=index),
                open=close - 0.2,
                high=close + 0.8,
                low=close - 0.8,
                close=close,
                volume=100_000 if index < days - 1 else 120_000,
            )
        )
    if future_spike:
        bars.append(
            PullbackBar(
                symbol="AAA",
                session_date=start + timedelta(days=len(closes)),
                open=500.0,
                high=1_000.0,
                low=400.0,
                close=999.0,
                volume=9_999_999,
            )
        )
    return bars


def _request(*, bars: list[PullbackBar] | None = None, quote_price: float | None = None) -> PullbackSignalInput:
    selected = bars or _bars()
    cutoff = selected[-1].session_date
    if selected[-1].close == 999.0:
        cutoff = selected[-2].session_date
    close = next(bar.close for bar in selected if bar.session_date == cutoff)
    evaluated_at = datetime(2026, 6, 10, 1, 0, tzinfo=timezone.utc)
    return PullbackSignalInput(
        strategy_id="pullback_trend_v2",
        recipe_version="2.0",
        symbol="AAA",
        signal_date=cutoff,
        bars=selected,
        multifactor_score=75.0,
        quote_price=quote_price or close,
        quote_as_of=evaluated_at,
        evaluated_at=evaluated_at,
    )


def test_pullback_contract_rejects_inconsistent_parameters_and_bars() -> None:
    with pytest.raises(ValidationError):
        PullbackTrendParameters(risk_window=120, trend_window=120)
    with pytest.raises(ValidationError):
        PullbackBar(
            symbol="AAA",
            session_date=date(2026, 1, 1),
            open=100,
            high=99,
            low=98,
            close=100,
            volume=1,
        )


def test_pullback_module_has_no_execution_or_broker_dependencies() -> None:
    source = inspect.getsource(pullback_module)
    forbidden = (
        "packages.brokers",
        "packages.db",
        "core.execution",
        "harness_service",
        "services.api",
        "OrderPlan",
        "OrderIntent",
    )
    assert all(token not in source for token in forbidden)


@pytest.mark.xfail(reason="QP-110 Claude implementation target", strict=False)
def test_pullback_indicators_ignore_future_rows() -> None:
    baseline = build_pullback_indicators(_request())
    with_future = build_pullback_indicators(_request(bars=_bars(future_spike=True)))

    assert with_future == baseline
    assert baseline.completed_sessions == 135
    assert baseline.sma120 > 0
    assert baseline.atr14 >= 0


@pytest.mark.xfail(reason="QP-110 Claude implementation target", strict=False)
def test_pullback_entry_requires_every_locked_confirmation() -> None:
    decision = evaluate_pullback_signal(_request())

    assert decision.action == SignalAction.buy_ready
    assert decision.indicators is not None
    assert decision.indicators.close > decision.indicators.sma120
    assert decision.indicators.prior_rsi14 < 35 <= decision.indicators.rsi14
    assert decision.indicators.volume_ratio20 >= 1.05
    assert decision.target_weight_hint <= 0.15


@pytest.mark.xfail(reason="QP-110 Claude implementation target", strict=False)
def test_pullback_entry_accepts_exact_score_and_premium_boundaries() -> None:
    request = _request()
    cutoff_close = next(bar.close for bar in request.bars if bar.session_date == request.signal_date)
    request = request.model_copy(
        update={
            "multifactor_score": 68.0,
            "quote_price": cutoff_close * 1.005,
            "quote_as_of": request.evaluated_at - timedelta(seconds=30),
        }
    )

    decision = evaluate_pullback_signal(request)

    assert decision.action == SignalAction.buy_ready
    assert decision.quote_age_seconds == 30.0


@pytest.mark.xfail(reason="QP-110 Claude implementation target", strict=False)
def test_pullback_insufficient_history_fails_closed() -> None:
    request = _request(bars=_bars()[:119])

    decision = evaluate_pullback_signal(request)

    assert decision.action == SignalAction.blocked
    assert decision.indicators is None
    assert "insufficient_history" in decision.reason_codes


@pytest.mark.xfail(reason="QP-110 Claude implementation target", strict=False)
def test_pullback_ineligible_candidate_fails_closed_before_buy() -> None:
    request = _request().model_copy(
        update={"candidate_eligible": False, "candidate_block_reason": "policy_blocklist"}
    )

    decision = evaluate_pullback_signal(request)

    assert decision.action == SignalAction.blocked
    assert "policy_blocklist" in decision.reason_codes


@pytest.mark.xfail(reason="QP-110 Claude implementation target", strict=False)
def test_pullback_future_or_naive_quote_timestamp_fails_closed() -> None:
    request = _request()
    future = request.model_copy(update={"quote_as_of": request.evaluated_at + timedelta(seconds=1)})
    naive = request.model_copy(
        update={
            "quote_as_of": request.quote_as_of.replace(tzinfo=None),
            "evaluated_at": request.evaluated_at.replace(tzinfo=None),
        }
    )

    assert evaluate_pullback_signal(future).action == SignalAction.blocked
    assert evaluate_pullback_signal(naive).action == SignalAction.blocked


@pytest.mark.xfail(reason="QP-110 Claude implementation target", strict=False)
def test_pullback_decision_is_deterministic_and_preserves_strategy_identity() -> None:
    request = _request()

    first = evaluate_pullback_signal(request)
    second = evaluate_pullback_signal(request)

    assert first == second
    assert first.strategy_id == request.strategy_id
    assert first.recipe_version == request.recipe_version


@pytest.mark.xfail(reason="QP-110 Claude implementation target", strict=False)
def test_pullback_stale_quote_fails_closed() -> None:
    request = _request()
    request = request.model_copy(update={"quote_as_of": request.evaluated_at - timedelta(seconds=31)})

    decision = evaluate_pullback_signal(request)

    assert decision.action == SignalAction.blocked
    assert "quote_stale" in decision.reason_codes
    assert decision.target_weight_hint == request.current_weight


@pytest.mark.xfail(reason="QP-110 Claude implementation target", strict=False)
def test_pullback_existing_position_exit_precedes_overheat_trim() -> None:
    request = _request(bars=_bars(final_close=90.0)).model_copy(update={"current_weight": 0.10})
    decision = evaluate_pullback_signal(request)

    assert decision.indicators is not None
    assert decision.indicators.close <= decision.indicators.sma20 * 0.94
    assert decision.action == SignalAction.exit
    assert decision.target_weight_hint == 0.0
