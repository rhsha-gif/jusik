from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quantpilot.packages.core.marketdata.fixture_provider import (
    default_ohlcv_fixture_path as _default_ohlcv_fixture_path,
    load_fixture_ohlcv as _load_fixture_ohlcv,
)
from quantpilot.packages.core.marketdata.providers import OHLCVProvider, QuoteProvider
from quantpilot.packages.core.marketdata.types import (
    MarketDataQuality,
    ProviderStatus,
    QuoteSnapshot,
    SignalSet,
)
from quantpilot.packages.core.schemas import (
    CandidateUniverseItem,
    Signal,
    SignalAction,
    StrategyRecipe,
    TechnicalIndicatorSnapshot,
    UserPolicy,
    utc_now,
)
from quantpilot.packages.core.technical.indicators import calculate_technical_indicators, fixture_price_history
from quantpilot.packages.core.universe.builder import build_candidate_universe


def default_ohlcv_fixture_path() -> Path:
    return _default_ohlcv_fixture_path()


def load_fixture_ohlcv(path: Path | None = None) -> list[dict[str, Any]]:
    return _load_fixture_ohlcv(path)


def classify_fixture_bar(bar: dict[str, Any]) -> tuple[SignalAction, float, str]:
    if bar.get("blocked", False):
        return SignalAction.blocked, 0.0, "blocked by fixture trading halt"

    close = float(bar["close"])
    ma20 = float(bar["ma20"])
    rsi = float(bar["rsi"])
    volume_ratio = float(bar.get("volume_ratio", 1.0))
    position_weight = float(bar.get("position_weight", 0.0))

    if position_weight > 0 and close <= ma20 * 0.94:
        return SignalAction.exit, 1.0, "risk moving average broke"
    if position_weight > 0 and (rsi >= 72 or close >= ma20 * 1.2):
        return SignalAction.trim, 0.7, "position overheated"
    if position_weight > 0:
        return SignalAction.hold, 0.4, "position remains within risk band"
    if close > ma20 and rsi <= 35 and volume_ratio >= 1.1:
        return SignalAction.buy_ready, 0.8, "pullback recovered with volume"
    if close > ma20 and rsi <= 45:
        return SignalAction.buy_wait, 0.3, "setup is forming but not ready"
    return SignalAction.watch, 0.1, "no actionable setup"


def classify_level2_action(
    policy: UserPolicy,
    candidate: CandidateUniverseItem,
    indicator: TechnicalIndicatorSnapshot,
    *,
    current_weight: float = 0.0,
) -> SignalAction:
    if candidate.block_reason is not None:
        return SignalAction.blocked
    if current_weight > 0 and indicator.close <= indicator.moving_averages["ma20"] * 0.94:
        return SignalAction.exit
    if current_weight > policy.max_position_weight:
        return SignalAction.trim
    if current_weight > 0 and (indicator.rsi >= 72 or indicator.close >= indicator.moving_averages["ma20"] * 1.2):
        return SignalAction.trim
    if current_weight > 0:
        return SignalAction.hold
    if indicator.technical_score >= 68 and indicator.volume_ratio >= 1.05 and indicator.rsi <= 65:
        return SignalAction.buy_ready
    if indicator.technical_score >= 52 and indicator.liquidity_score >= 40:
        return SignalAction.buy_wait
    if not candidate.theme_match:
        return SignalAction.watch
    return SignalAction.watch


def _score_from_fixture(bar: dict[str, Any]) -> tuple[float, float]:
    close = float(bar["close"])
    ma20 = float(bar["ma20"])
    rsi = float(bar["rsi"])
    volume_ratio = float(bar.get("volume_ratio", 1.0))
    trend_score = max(0.0, min(100.0, 50 + (close / ma20 - 1) * 250))
    rsi_score = max(0.0, min(100.0, 100 - abs(rsi - 50) * 1.7))
    volume_score = max(0.0, min(100.0, 50 + (volume_ratio - 1) * 35))
    technical_score = max(0.0, min(100.0, trend_score * 0.45 + rsi_score * 0.35 + volume_score * 0.20))
    quant_score = max(0.0, min(100.0, technical_score * 0.75 + volume_score * 0.25))
    return round(technical_score, 6), round(quant_score, 6)


def _target_weight_hint(policy: UserPolicy | None, action: SignalAction, strength: float, position_weight: float) -> float:
    if policy is None:
        max_weight = 0.15
    else:
        max_weight = policy.max_position_weight
    if action == SignalAction.buy_ready:
        return round(min(max_weight, max(0.01, strength * max_weight)), 6)
    if action == SignalAction.buy_wait:
        return 0.0
    if action == SignalAction.trim:
        return round(max(0.0, min(position_weight * 0.5, max_weight)), 6)
    if action == SignalAction.hold:
        return round(min(position_weight, max_weight), 6)
    if action in {SignalAction.exit, SignalAction.blocked}:
        return 0.0
    return round(position_weight, 6)


def _reason_codes(action: SignalAction, reason: str) -> list[str]:
    if action == SignalAction.blocked:
        return ["blocked", reason.replace(" ", "_")]
    if action == SignalAction.exit:
        return ["risk_exit", "moving_average_break"]
    if action == SignalAction.trim:
        return ["rebalance_trim", "overheat_or_policy_cap"]
    if action == SignalAction.hold:
        return ["position_hold", "within_risk_band"]
    if action == SignalAction.buy_ready:
        return ["setup_ready", "trend_volume_pullback"]
    if action == SignalAction.buy_wait:
        return ["setup_forming", "confirmation_needed"]
    return ["watchlist", "no_actionable_setup"]


def _signal_from_fixture_bar(
    recipe: StrategyRecipe,
    bar: dict[str, Any],
    *,
    policy: UserPolicy | None,
    signal_date: date,
) -> Signal:
    action, strength, reason = classify_fixture_bar(bar)
    technical_score, quant_score = _score_from_fixture(bar)
    close = float(bar["close"])
    position_weight = float(bar.get("position_weight", 0.0))
    return Signal(
        strategy_id=recipe.strategy_id,
        recipe_version=recipe.version,
        symbol=str(bar["symbol"]),
        ticker=str(bar["symbol"]),
        signal_date=signal_date,
        action=action,
        strength=strength,
        technical_score=technical_score,
        quant_score=quant_score,
        target_weight_hint=_target_weight_hint(policy, action, strength, position_weight),
        stop_price_hint=round(close * 0.92, 4) if action in {SignalAction.buy_ready, SignalAction.buy_wait, SignalAction.hold} else None,
        take_profit_hint=round(close * 1.18, 4) if action in {SignalAction.buy_ready, SignalAction.buy_wait, SignalAction.hold, SignalAction.trim} else None,
        valid_until=signal_date + fixture_signal_validity(),
        policy_version=policy.version if policy else None,
        reason_codes=_reason_codes(action, reason),
        reason=reason,
        source="fixture_level_1_2_signal_engine",
    )


def load_signal_date() -> date:
    return date.fromisoformat(str(fixture_price_history()[-1]["date"]))


def fixture_signal_validity() -> timedelta:
    return timedelta(days=5)


def _legacy_fixture_bars(bars: list[dict[str, Any]]) -> bool:
    return bool(bars) and "ma20" in bars[0] and "rsi" in bars[0]


def generate_signals(
    recipe: StrategyRecipe,
    bars: list[dict[str, Any]],
    *,
    policy: UserPolicy | None = None,
    securities: list[dict[str, Any]] | None = None,
) -> list[Signal]:
    signals: list[Signal] = []
    signal_date = load_signal_date()
    if _legacy_fixture_bars(bars):
        for bar in bars:
            signals.append(_signal_from_fixture_bar(recipe, bar, policy=policy, signal_date=signal_date))
        return signals

    if policy is None:
        policy = UserPolicy()
    candidates = {candidate.ticker: candidate for candidate in build_candidate_universe(policy, securities)}
    for ticker, candidate in candidates.items():
        indicator = calculate_technical_indicators(bars, ticker=ticker, signal_date=signal_date)
        current_weight = 0.0
        action = classify_level2_action(policy, candidate, indicator, current_weight=current_weight)
        strength = round(indicator.quant_score / 100, 6)
        close = indicator.close
        reason = f"technical_score={indicator.technical_score:.1f}; quant_score={indicator.momentum_score:.1f}"
        signals.append(
            Signal(
                strategy_id=recipe.strategy_id,
                recipe_version=recipe.version,
                symbol=ticker,
                ticker=ticker,
                signal_date=indicator.signal_date,
                action=action,
                strength=strength,
                technical_score=indicator.technical_score,
                quant_score=indicator.momentum_score,
                target_weight_hint=_target_weight_hint(policy, action, strength, current_weight),
                stop_price_hint=round(close * 0.92, 4) if action in {SignalAction.buy_ready, SignalAction.buy_wait, SignalAction.hold} else None,
                take_profit_hint=round(close * 1.18, 4) if action in {SignalAction.buy_ready, SignalAction.buy_wait, SignalAction.hold, SignalAction.trim} else None,
                valid_until=indicator.signal_date + fixture_signal_validity(),
                policy_version=policy.version,
                reason_codes=_reason_codes(action, reason),
                reason=reason,
                source="fixture_level_1_2_signal_engine",
            )
        )
    return signals


def _symbol_key(value: Any) -> str:
    return str(value).strip().upper()


def _bar_symbols(bars: list[dict[str, Any]]) -> list[str]:
    symbols = sorted(
        {
            _symbol_key(bar.get("symbol", bar.get("ticker", "")))
            for bar in bars
            if _symbol_key(bar.get("symbol", bar.get("ticker", "")))
        }
    )
    return symbols


def _security_symbols(securities: list[dict[str, Any]] | None) -> list[str]:
    if securities is None:
        return []
    return sorted(
        {
            _symbol_key(security.get("ticker", security.get("symbol", "")))
            for security in securities
            if _symbol_key(security.get("ticker", security.get("symbol", "")))
        }
    )


def _issue_codes_for_status(channel: str, status: ProviderStatus) -> list[str]:
    if status.state == "unavailable":
        return ["provider_unavailable", f"{channel}_provider_unavailable"]
    if status.state == "stale":
        return ["provider_stale", f"{channel}_provider_stale"]
    return []


def _unique_codes(codes: list[str]) -> list[str]:
    return list(dict.fromkeys(codes))


def _combined_quality(
    *,
    provider_status: dict[str, ProviderStatus],
    qualities: list[MarketDataQuality],
    symbol_count: int,
) -> MarketDataQuality:
    usable = True
    degraded = False
    reason_codes: list[str] = []
    data_mode = qualities[0].data_mode if qualities else None

    for channel, status in provider_status.items():
        status_codes = _issue_codes_for_status(channel, status)
        if status_codes:
            usable = False
            degraded = True
            reason_codes.extend(status_codes)
        if data_mode is None:
            data_mode = status.data_mode

    for quality in qualities:
        if not quality.usable:
            usable = False
        if quality.degraded:
            degraded = True
        reason_codes.extend(quality.reason_codes)
        data_mode = quality.data_mode

    return MarketDataQuality(
        usable=usable,
        degraded=degraded,
        reason_codes=_unique_codes(reason_codes),
        symbol_count=symbol_count,
        data_mode=data_mode or provider_status.get("ohlcv", ProviderStatus(provider_name="unknown")).data_mode,
    )


def _blocked_signal(
    recipe: StrategyRecipe,
    symbol: str,
    *,
    policy: UserPolicy | None,
    signal_date: date,
    reason: str,
    reason_codes: list[str],
) -> Signal:
    return Signal(
        strategy_id=recipe.strategy_id,
        recipe_version=recipe.version,
        symbol=symbol,
        ticker=symbol,
        signal_date=signal_date,
        action=SignalAction.blocked,
        strength=0.0,
        technical_score=0.0,
        quant_score=0.0,
        target_weight_hint=0.0,
        stop_price_hint=None,
        take_profit_hint=None,
        valid_until=signal_date + fixture_signal_validity(),
        policy_version=policy.version if policy else None,
        reason_codes=_unique_codes(["provider_fail_closed", *reason_codes]),
        reason=reason,
        source="provider_bound_signal_engine",
    )


def _blocked_signals(
    recipe: StrategyRecipe,
    symbols: list[str],
    *,
    policy: UserPolicy | None,
    reason: str,
    reason_codes: list[str],
) -> list[Signal]:
    signal_date = load_signal_date() if symbols else utc_now().date()
    return [
        _blocked_signal(
            recipe,
            symbol,
            policy=policy,
            signal_date=signal_date,
            reason=reason,
            reason_codes=reason_codes,
        )
        for symbol in symbols
    ]


def _provider_failure_reason(statuses: dict[str, ProviderStatus], quality: MarketDataQuality) -> str:
    for channel, status in statuses.items():
        if status.state != "available":
            detail = f": {status.reason}" if status.reason else ""
            return f"{channel} provider {status.state}{detail}"
    if quality.reason_codes:
        return f"market data quality fail-closed: {', '.join(quality.reason_codes)}"
    return "market data provider fail-closed"


def generate_provider_bound_signals(
    recipe: StrategyRecipe,
    ohlcv_provider: OHLCVProvider,
    *,
    quote_provider: QuoteProvider | None = None,
    policy: UserPolicy | None = None,
    securities: list[dict[str, Any]] | None = None,
    horizon: str | None = None,
) -> SignalSet:
    requested_symbols = _security_symbols(securities)
    provider_status: dict[str, ProviderStatus] = {}
    qualities: list[MarketDataQuality] = []

    try:
        ohlcv = ohlcv_provider.get_ohlcv(requested_symbols or None, horizon=horizon)
    except Exception as exc:
        status = ProviderStatus(
            provider_name=type(ohlcv_provider).__name__,
            state="unavailable",
            reason=str(exc),
        )
        provider_status["ohlcv"] = status
        quality = _combined_quality(
            provider_status=provider_status,
            qualities=[],
            symbol_count=len(requested_symbols),
        )
        return SignalSet(
            signals=_blocked_signals(
                recipe,
                requested_symbols,
                policy=policy,
                reason=_provider_failure_reason(provider_status, quality),
                reason_codes=quality.reason_codes,
            ),
            provider_status=provider_status,
            data_quality=quality,
        )

    bars = ohlcv.bars
    provider_status["ohlcv"] = ohlcv.provider_status
    qualities.append(ohlcv.data_quality)
    signal_symbols = _bar_symbols(bars) or requested_symbols

    if quote_provider is not None:
        try:
            quote_snapshot: QuoteSnapshot = quote_provider.get_quotes(signal_symbols)
        except Exception as exc:
            provider_status["quote"] = ProviderStatus(
                provider_name=type(quote_provider).__name__,
                state="unavailable",
                reason=str(exc),
            )
        else:
            provider_status["quote"] = quote_snapshot.provider_status
            qualities.append(quote_snapshot.data_quality)

    quality = _combined_quality(
        provider_status=provider_status,
        qualities=qualities,
        symbol_count=len(signal_symbols),
    )
    if not quality.usable:
        return SignalSet(
            signals=_blocked_signals(
                recipe,
                signal_symbols,
                policy=policy,
                reason=_provider_failure_reason(provider_status, quality),
                reason_codes=quality.reason_codes,
            ),
            provider_status=provider_status,
            data_quality=quality,
        )

    signals = generate_signals(recipe, bars, policy=policy, securities=securities)
    return SignalSet(
        signals=signals,
        provider_status=provider_status,
        data_quality=quality.model_copy(update={"symbol_count": len(signals)}),
    )
