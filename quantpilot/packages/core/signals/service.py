from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quantpilot.packages.core.marketdata.fixture_provider import (
    default_ohlcv_fixture_path as _default_ohlcv_fixture_path,
    load_fixture_ohlcv as _load_fixture_ohlcv,
)
from quantpilot.packages.core.marketdata.providers import OHLCVProvider, QuoteProvider
from quantpilot.packages.core.marketdata.types import (
    MarketDataQuality,
    ProviderStatus,
    Quote,
    QuoteSnapshot,
    SignalSet,
)
from quantpilot.packages.core.schemas import (
    CandidateUniverseItem,
    PortfolioSnapshot,
    Signal,
    SignalAction,
    StrategyRecipe,
    TechnicalIndicatorSnapshot,
    UserPolicy,
    utc_now,
)
from quantpilot.packages.core.signals.calibration import calibrate_signal_set
from quantpilot.packages.core.signals.multifactor import build_multi_factor_score
from quantpilot.packages.core.signals.pullback_trend import (
    PullbackBar,
    PullbackSignalInput,
    PullbackTrendParameters,
    build_pullback_indicators,
    evaluate_pullback_signal,
)
from quantpilot.packages.core.technical.indicators import calculate_technical_indicators, fixture_price_history
from quantpilot.packages.core.universe.builder import build_candidate_universe, build_ranked_candidate_universe


PROFESSIONAL_STRATEGY_ID = "pullback_trend_v2"
PROFESSIONAL_MARKET_TIMEZONE = ZoneInfo("Asia/Seoul")


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
        entry_atr14=None,
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


def _bar_session_date(bar: dict[str, Any]) -> date | None:
    value = bar.get("date", bar.get("session_date"))
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _professional_parameters(recipe: StrategyRecipe) -> PullbackTrendParameters | None:
    rules = getattr(recipe, "decision_rules", None)
    if rules is None:
        return None
    supported = PullbackTrendParameters.model_fields
    payload = {
        key: value
        for key, value in rules.model_dump().items()
        if key in supported
    }
    return PullbackTrendParameters(**payload)


def _portfolio_weight(snapshot: PortfolioSnapshot | None, symbol: str) -> float:
    if snapshot is None:
        return 0.0
    normalized = _symbol_key(symbol)
    value = sum(
        position.market_value
        for position in snapshot.positions
        if _symbol_key(position.symbol) == normalized
    )
    return round(value / snapshot.equity, 6)


def _decision_signal(
    *,
    recipe: StrategyRecipe,
    policy: UserPolicy,
    decision,
    technical_score: float,
    quant_score: float,
    score_reason_codes: list[str],
) -> Signal:
    return Signal(
        strategy_id=decision.strategy_id,
        recipe_version=decision.recipe_version,
        symbol=decision.symbol,
        ticker=decision.symbol,
        signal_date=decision.signal_date,
        action=decision.action,
        strength=decision.strength,
        technical_score=technical_score,
        quant_score=quant_score,
        target_weight_hint=decision.target_weight_hint,
        stop_price_hint=None,
        take_profit_hint=None,
        entry_atr14=(
            decision.indicators.atr14
            if decision.indicators is not None
            else None
        ),
        valid_until=decision.signal_date + fixture_signal_validity(),
        policy_version=policy.version,
        reason_codes=_unique_codes([*decision.reason_codes, *score_reason_codes]),
        reason=decision.reason,
        source="professional_pullback_trend_v2",
    )


def _apply_max_positions_cap(
    signals: list[Signal],
    *,
    policy: UserPolicy,
    portfolio_snapshot: PortfolioSnapshot | None,
    multifactor_scores: dict[str, float],
) -> list[Signal]:
    held_symbols = {
        _symbol_key(position.symbol)
        for position in (portfolio_snapshot.positions if portfolio_snapshot is not None else [])
        if position.quantity > 0
    }
    available_slots = max(0, policy.max_positions - len(held_symbols))
    new_buys = sorted(
        (
            signal
            for signal in signals
            if signal.action == SignalAction.buy_ready and _symbol_key(signal.symbol) not in held_symbols
        ),
        key=lambda signal: (-multifactor_scores.get(_symbol_key(signal.symbol), 0.0), _symbol_key(signal.symbol)),
    )
    allowed = {_symbol_key(signal.symbol) for signal in new_buys[:available_slots]}
    capped: list[Signal] = []
    for signal in signals:
        symbol = _symbol_key(signal.symbol)
        if signal.action != SignalAction.buy_ready or symbol in held_symbols or symbol in allowed:
            capped.append(signal)
            continue
        capped.append(
            signal.model_copy(
                update={
                    "action": SignalAction.watch,
                    "strength": 0.0,
                    "target_weight_hint": 0.0,
                    "stop_price_hint": None,
                    "take_profit_hint": None,
                    "reason_codes": _unique_codes([*signal.reason_codes, "max_positions_cap"]),
                    "reason": f"{signal.reason}; new position blocked by max_positions cap",
                }
            )
        )
    return capped


def _generate_professional_signals(
    *,
    recipe: StrategyRecipe,
    bars: list[dict[str, Any]],
    quotes: dict[str, Quote],
    quality: MarketDataQuality,
    policy: UserPolicy | None,
    securities: list[dict[str, Any]] | None,
    portfolio_snapshot: PortfolioSnapshot | None,
    evaluated_at: datetime,
) -> list[Signal]:
    effective_policy = policy or UserPolicy()
    symbols = _bar_symbols(bars) or _security_symbols(securities)
    parameters = _professional_parameters(recipe)
    if parameters is None:
        return _blocked_signals(
            recipe,
            symbols,
            policy=effective_policy,
            reason="typed decision rules are required for pullback_trend_v2",
            reason_codes=["typed_decision_rules_missing"],
        )
    if securities is None:
        return _blocked_signals(
            recipe,
            symbols,
            policy=effective_policy,
            reason="security metadata is required for professional candidate selection",
            reason_codes=["security_metadata_missing"],
        )

    rules = recipe.decision_rules
    assert rules is not None
    ranked = build_ranked_candidate_universe(
        effective_policy,
        securities,
        portfolio_snapshot=portfolio_snapshot,
        max_candidates=rules.max_candidates,
        include_excluded=True,
    )
    ranked_by_symbol = {_symbol_key(item.candidate.ticker): item for item in ranked}
    quotes_by_symbol = {_symbol_key(symbol): quote for symbol, quote in quotes.items()}
    cutoff = (
        evaluated_at.astimezone(PROFESSIONAL_MARKET_TIMEZONE).date()
        if evaluated_at.tzinfo is not None and evaluated_at.utcoffset() is not None
        else evaluated_at.date()
    )
    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    invalid_date_symbols: set[str] = set()
    for bar in bars:
        symbol = _symbol_key(bar.get("symbol", bar.get("ticker", "")))
        if not symbol:
            continue
        session_date = _bar_session_date(bar)
        if session_date is None:
            invalid_date_symbols.add(symbol)
            continue
        # Without an exchange-calendar completion marker, the evaluation-date
        # daily bar is conservatively treated as still forming.
        if session_date < cutoff:
            rows_by_symbol.setdefault(symbol, []).append({**bar, "date": session_date.isoformat()})
    for symbol_rows in rows_by_symbol.values():
        symbol_rows.sort(key=lambda row: str(row["date"]))

    generated: list[Signal] = []
    multifactor_scores: dict[str, float] = {}
    for symbol in symbols:
        symbol_rows = rows_by_symbol.get(symbol, [])
        signal_date = _bar_session_date(symbol_rows[-1]) if symbol_rows else cutoff
        if not symbol_rows or signal_date is None:
            generated.append(
                _blocked_signal(
                    recipe,
                    symbol,
                    policy=effective_policy,
                    signal_date=cutoff,
                    reason="completed dated history is missing",
                    reason_codes=["completed_history_missing"],
                )
            )
            continue
        quote = quotes_by_symbol.get(symbol)
        if quote is None:
            generated.append(
                _blocked_signal(
                    recipe,
                    symbol,
                    policy=effective_policy,
                    signal_date=signal_date,
                    reason="actual quote evidence is missing",
                    reason_codes=["quote_missing"],
                )
            )
            continue

        try:
            pullback_bars = [
                PullbackBar(
                    symbol=symbol,
                    session_date=date.fromisoformat(str(row["date"])),
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row.get("volume", 0),
                )
                for row in symbol_rows
            ]
        except (KeyError, TypeError, ValueError):
            generated.append(
                _blocked_signal(
                    recipe,
                    symbol,
                    policy=effective_policy,
                    signal_date=signal_date,
                    reason="completed history failed OHLCV validation",
                    reason_codes=["completed_history_invalid"],
                )
            )
            continue

        ranked_candidate = ranked_by_symbol.get(symbol)
        candidate_eligible = bool(ranked_candidate and ranked_candidate.selected)
        candidate_reason = (
            ranked_candidate.exclusion_reason
            if ranked_candidate is not None
            else "candidate_metadata_missing"
        )
        request = PullbackSignalInput(
            strategy_id=recipe.strategy_id,
            recipe_version=recipe.version,
            symbol=symbol,
            signal_date=signal_date,
            bars=pullback_bars,
            current_weight=_portfolio_weight(portfolio_snapshot, symbol),
            max_position_weight=effective_policy.max_position_weight,
            candidate_eligible=candidate_eligible,
            candidate_block_reason=candidate_reason,
            data_usable=quality.usable and symbol not in invalid_date_symbols,
            multifactor_score=0.0,
            quote_price=quote.last,
            quote_as_of=quote.as_of,
            evaluated_at=evaluated_at,
        )

        technical_score = 0.0
        quant_score = 0.0
        score_reason_codes: list[str] = []
        if candidate_eligible and request.data_usable:
            try:
                indicators = build_pullback_indicators(request, parameters)
                technical = calculate_technical_indicators(
                    symbol_rows,
                    ticker=symbol,
                    signal_date=signal_date,
                )
            except ValueError:
                pass
            else:
                technical_score = technical.technical_score
                quant_score = technical.momentum_score
                provisional = Signal(
                    strategy_id=recipe.strategy_id,
                    recipe_version=recipe.version,
                    symbol=symbol,
                    signal_date=signal_date,
                    action=SignalAction.watch,
                    strength=round(technical.momentum_score / 100.0, 6),
                    technical_score=technical_score,
                    quant_score=quant_score,
                    target_weight_hint=0.0,
                    policy_version=effective_policy.version,
                    reason_codes=["professional_multifactor_provisional"],
                    reason="provisional score input; never exposed to planning",
                    source="professional_multifactor_provisional",
                )
                latest_bar = {
                    **symbol_rows[-1],
                    "ma20": indicators.sma20,
                    "volume_ratio": indicators.volume_ratio20,
                }
                score = build_multi_factor_score(
                    signal=provisional,
                    bar=latest_bar,
                    market_data_quality=quality,
                    ranked_candidate=ranked_candidate,
                )
                multifactor_scores[symbol] = score.final_score
                score_reason_codes = score.reason_codes
                request = request.model_copy(update={"multifactor_score": score.final_score})

        decision = evaluate_pullback_signal(request, parameters)
        generated.append(
            _decision_signal(
                recipe=recipe,
                policy=effective_policy,
                decision=decision,
                technical_score=technical_score,
                quant_score=quant_score,
                score_reason_codes=score_reason_codes,
            )
        )

    return _apply_max_positions_cap(
        generated,
        policy=effective_policy,
        portfolio_snapshot=portfolio_snapshot,
        multifactor_scores=multifactor_scores,
    )


def generate_provider_bound_signals(
    recipe: StrategyRecipe,
    ohlcv_provider: OHLCVProvider,
    *,
    quote_provider: QuoteProvider | None = None,
    policy: UserPolicy | None = None,
    securities: list[dict[str, Any]] | None = None,
    horizon: str | None = None,
    portfolio_snapshot: PortfolioSnapshot | None = None,
    evaluated_at: datetime | None = None,
) -> SignalSet:
    evaluation_time = evaluated_at
    requested_symbols = _security_symbols(securities)
    provider_status: dict[str, ProviderStatus] = {}
    qualities: list[MarketDataQuality] = []
    quote_snapshot: QuoteSnapshot | None = None

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
        blocked_signals = _blocked_signals(
            recipe,
            requested_symbols,
            policy=policy,
            reason=_provider_failure_reason(provider_status, quality),
            reason_codes=quality.reason_codes,
        )
        return SignalSet(
            signals=blocked_signals,
            provider_status=provider_status,
            data_quality=quality,
            quotes={},
            calibrated_signal_set=calibrate_signal_set(
                signals=blocked_signals,
                bars=[],
                provider_status=provider_status,
                market_data_quality=quality,
                policy=policy,
                securities=securities,
                horizon=horizon,
            ),
        )

    bars = ohlcv.bars
    provider_status["ohlcv"] = ohlcv.provider_status
    qualities.append(ohlcv.data_quality)
    signal_symbols = _bar_symbols(bars) or requested_symbols

    if quote_provider is not None:
        try:
            quote_snapshot = quote_provider.get_quotes(signal_symbols)
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
        blocked_signals = _blocked_signals(
            recipe,
            signal_symbols,
            policy=policy,
            reason=_provider_failure_reason(provider_status, quality),
            reason_codes=quality.reason_codes,
        )
        return SignalSet(
            signals=blocked_signals,
            provider_status=provider_status,
            data_quality=quality,
            quotes=quote_snapshot.quotes if quote_snapshot is not None else {},
            calibrated_signal_set=calibrate_signal_set(
                signals=blocked_signals,
                bars=bars,
                provider_status=provider_status,
                market_data_quality=quality,
                policy=policy,
                securities=securities,
                horizon=horizon,
            ),
        )

    if recipe.strategy_id == PROFESSIONAL_STRATEGY_ID:
        signals = _generate_professional_signals(
            recipe=recipe,
            bars=bars,
            quotes=quote_snapshot.quotes if quote_snapshot is not None else {},
            quality=quality,
            policy=policy,
            securities=securities,
            portfolio_snapshot=portfolio_snapshot,
            evaluated_at=evaluation_time or utc_now(),
        )
    else:
        signals = generate_signals(recipe, bars, policy=policy, securities=securities)
    calibrated = calibrate_signal_set(
        signals=signals,
        bars=bars,
        provider_status=provider_status,
        market_data_quality=quality.model_copy(update={"symbol_count": len(signals)}),
        policy=policy,
        securities=securities,
        horizon=horizon,
    )
    return SignalSet(
        signals=signals,
        provider_status=provider_status,
        data_quality=quality.model_copy(update={"symbol_count": len(signals)}),
        quotes=quote_snapshot.quotes if quote_snapshot is not None else {},
        calibrated_signal_set=calibrated,
    )
