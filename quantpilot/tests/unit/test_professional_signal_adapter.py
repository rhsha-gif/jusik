from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from quantpilot.packages.core.marketdata.fake_provider import FakeOHLCVProvider
from quantpilot.packages.core.marketdata.types import (
    MarketDataQuality,
    ProviderStatus,
    Quote,
    QuoteSnapshot,
)
from quantpilot.packages.core.portfolio.planner import build_portfolio_plan
from quantpilot.packages.core.schemas import (
    PortfolioPosition,
    PortfolioSnapshot,
    SignalAction,
    UserPolicy,
)
from quantpilot.packages.core.signals.service import generate_provider_bound_signals
from quantpilot.packages.core.signals.types import MultiFactorScore
from quantpilot.packages.core.strategies.loader import load_strategy_recipe


EVALUATED_AT = datetime(2026, 6, 10, 1, 0, tzinfo=timezone.utc)


class StaticQuoteProvider:
    def __init__(self, quotes: dict[str, Quote]) -> None:
        self.quotes = quotes

    def get_quotes(self, symbols: list[str]) -> QuoteSnapshot:
        wanted = {symbol.upper() for symbol in symbols}
        selected = {
            symbol: quote
            for symbol, quote in self.quotes.items()
            if symbol.upper() in wanted
        }
        return QuoteSnapshot(
            quotes=selected,
            provider_status=ProviderStatus(provider_name="static_quote"),
            data_quality=MarketDataQuality(
                usable=True,
                degraded=False,
                symbol_count=len(selected),
            ),
        )


def _security(symbol: str) -> dict[str, object]:
    return {
        "ticker": symbol,
        "name": symbol,
        "market": "KR_STOCK",
        "sector": "technology",
        "themes": ["ai"],
        "avg_daily_value": 10_000_000,
        "data_ready": True,
    }


def _bars(
    symbol: str,
    *,
    future_spike: bool = False,
    evaluation_day_spike: bool = False,
) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    closes = [100.0 + index * 0.20 for index in range(120)]
    closes.extend([closes[-1] - offset for offset in range(1, 15)])
    closes.append(closes[-1] + 8.0)
    rows = [
        {
            "symbol": symbol,
            "ticker": symbol,
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close - 0.2,
            "high": close + 0.8,
            "low": close - 0.8,
            "close": close,
            "volume": 100_000 if index < len(closes) - 1 else 120_000,
        }
        for index, close in enumerate(closes)
    ]
    if future_spike:
        rows.append(
            {
                "symbol": symbol,
                "ticker": symbol,
                "date": "2026-12-31",
                "open": 500.0,
                "high": 1_000.0,
                "low": 400.0,
                "close": 999.0,
                "volume": 9_999_999,
            }
        )
    if evaluation_day_spike:
        rows.append(
            {
                "symbol": symbol,
                "ticker": symbol,
                "date": EVALUATED_AT.date().isoformat(),
                "open": 500.0,
                "high": 1_000.0,
                "low": 400.0,
                "close": 999.0,
                "volume": 9_999_999,
            }
        )
    return rows


def _quote(symbol: str, price: float) -> Quote:
    return Quote(symbol=symbol, last=price, as_of=EVALUATED_AT)


def _snapshot(*, positions: list[PortfolioPosition] | None = None) -> PortfolioSnapshot:
    active = positions or []
    return PortfolioSnapshot(
        cash=1_000_000 - sum(position.market_value for position in active),
        equity=1_000_000,
        positions=active,
    )


def _fixed_multifactor(symbol: str, final_score: float = 75.0) -> MultiFactorScore:
    return MultiFactorScore(
        symbol=symbol,
        momentum=75.0,
        trend=75.0,
        volume=75.0,
        volatility=75.0,
        data_quality=100.0,
        final_score=final_score,
        regime="uptrend",
        weights={
            "momentum": 0.24,
            "trend": 0.30,
            "volume": 0.18,
            "volatility": 0.16,
            "data_quality": 0.12,
        },
        reason_codes=["regime_uptrend"],
    )


def test_professional_adapter_uses_actual_quote_and_portfolio_weight(monkeypatch) -> None:
    bars = _bars("AAA")
    close = float(bars[-1]["close"])
    monkeypatch.setattr(
        "quantpilot.packages.core.signals.service.build_multi_factor_score",
        lambda **_: _fixed_multifactor("AAA"),
    )
    snapshot = _snapshot(
        positions=[PortfolioPosition(symbol="AAA", quantity=1_000, market_price=100.0)]
    )

    signal_set = generate_provider_bound_signals(
        load_strategy_recipe("pullback_trend_v2"),
        FakeOHLCVProvider(bars),
        quote_provider=StaticQuoteProvider({"AAA": _quote("AAA", close)}),
        policy=UserPolicy(),
        securities=[_security("AAA")],
        horizon="completed_history",
        portfolio_snapshot=snapshot,
        evaluated_at=EVALUATED_AT,
    )

    assert signal_set.signals[0].action == SignalAction.hold
    assert signal_set.signals[0].target_weight_hint == 0.1
    assert signal_set.signals[0].source == "professional_pullback_trend_v2"
    assert signal_set.quotes["AAA"].last == close
    assert signal_set.quotes["AAA"].as_of == EVALUATED_AT


def test_professional_adapter_ignores_future_bars(monkeypatch) -> None:
    baseline_bars = _bars("AAA")
    close = float(baseline_bars[-1]["close"])
    monkeypatch.setattr(
        "quantpilot.packages.core.signals.service.build_multi_factor_score",
        lambda **_: _fixed_multifactor("AAA"),
    )
    kwargs = {
        "quote_provider": StaticQuoteProvider({"AAA": _quote("AAA", close)}),
        "policy": UserPolicy(),
        "securities": [_security("AAA")],
        "portfolio_snapshot": _snapshot(),
        "evaluated_at": EVALUATED_AT,
    }

    baseline = generate_provider_bound_signals(
        load_strategy_recipe("pullback_trend_v2"),
        FakeOHLCVProvider(baseline_bars),
        **kwargs,
    )
    future = generate_provider_bound_signals(
        load_strategy_recipe("pullback_trend_v2"),
        FakeOHLCVProvider(_bars("AAA", future_spike=True)),
        **kwargs,
    )

    assert future.signals[0].action == baseline.signals[0].action == SignalAction.buy_ready
    assert future.signals[0].technical_score == baseline.signals[0].technical_score
    assert future.signals[0].quant_score == baseline.signals[0].quant_score
    assert baseline.signals[0].entry_atr14 is not None
    assert baseline.signals[0].entry_atr14 > 0
    assert future.signals[0].entry_atr14 == baseline.signals[0].entry_atr14


def test_professional_adapter_ignores_unconfirmed_evaluation_day_bar(monkeypatch) -> None:
    baseline_bars = _bars("AAA")
    close = float(baseline_bars[-1]["close"])
    monkeypatch.setattr(
        "quantpilot.packages.core.signals.service.build_multi_factor_score",
        lambda **_: _fixed_multifactor("AAA"),
    )
    kwargs = {
        "quote_provider": StaticQuoteProvider({"AAA": _quote("AAA", close)}),
        "policy": UserPolicy(),
        "securities": [_security("AAA")],
        "portfolio_snapshot": _snapshot(),
        "evaluated_at": EVALUATED_AT,
    }

    baseline = generate_provider_bound_signals(
        load_strategy_recipe("pullback_trend_v2"),
        FakeOHLCVProvider(baseline_bars),
        **kwargs,
    )
    with_forming_bar = generate_provider_bound_signals(
        load_strategy_recipe("pullback_trend_v2"),
        FakeOHLCVProvider(_bars("AAA", evaluation_day_spike=True)),
        **kwargs,
    )

    assert with_forming_bar.signals[0].action == baseline.signals[0].action
    assert with_forming_bar.signals[0].technical_score == baseline.signals[0].technical_score
    assert with_forming_bar.signals[0].quant_score == baseline.signals[0].quant_score


def test_professional_adapter_never_falls_back_without_rules_or_quote(monkeypatch) -> None:
    recipe = load_strategy_recipe("pullback_trend_v2").model_copy(update={"decision_rules": None})
    bars = _bars("AAA")
    monkeypatch.setattr(
        "quantpilot.packages.core.signals.service.build_multi_factor_score",
        lambda **_: _fixed_multifactor("AAA"),
    )

    missing_rules = generate_provider_bound_signals(
        recipe,
        FakeOHLCVProvider(bars),
        quote_provider=StaticQuoteProvider({"AAA": _quote("AAA", float(bars[-1]["close"]))}),
        policy=UserPolicy(),
        securities=[_security("AAA")],
        portfolio_snapshot=_snapshot(),
        evaluated_at=EVALUATED_AT,
    )
    missing_quote = generate_provider_bound_signals(
        load_strategy_recipe("pullback_trend_v2"),
        FakeOHLCVProvider(bars),
        quote_provider=StaticQuoteProvider({}),
        policy=UserPolicy(),
        securities=[_security("AAA")],
        portfolio_snapshot=_snapshot(),
        evaluated_at=EVALUATED_AT,
    )

    assert missing_rules.signals[0].action == SignalAction.blocked
    assert "typed_decision_rules_missing" in missing_rules.signals[0].reason_codes
    assert missing_quote.signals[0].action == SignalAction.blocked
    assert "quote_missing" in missing_quote.signals[0].reason_codes


def test_blocked_stale_quote_never_liquidates_an_existing_position(monkeypatch) -> None:
    bars = _bars("AAA")
    close = float(bars[-1]["close"])
    monkeypatch.setattr(
        "quantpilot.packages.core.signals.service.build_multi_factor_score",
        lambda **_: _fixed_multifactor("AAA"),
    )
    snapshot = _snapshot(
        positions=[PortfolioPosition(symbol="AAA", quantity=1_000, market_price=100.0)]
    )
    stale_quote = Quote(
        symbol="AAA",
        last=close,
        as_of=EVALUATED_AT - timedelta(seconds=31),
    )

    signal_set = generate_provider_bound_signals(
        load_strategy_recipe("pullback_trend_v2"),
        FakeOHLCVProvider(bars),
        quote_provider=StaticQuoteProvider({"AAA": stale_quote}),
        policy=UserPolicy(),
        securities=[_security("AAA")],
        portfolio_snapshot=snapshot,
        evaluated_at=EVALUATED_AT,
    )
    plan = build_portfolio_plan(
        policy=UserPolicy(),
        signals=signal_set.signals,
        snapshot=snapshot,
        quotes={"AAA": stale_quote.last},
        quote_times={"AAA": stale_quote.as_of},
        require_explicit_quotes=True,
        rebalance_band=0.01,
    )

    assert signal_set.signals[0].action == SignalAction.blocked
    assert "quote_stale" in signal_set.signals[0].reason_codes
    assert plan.order_intents == []
    assert plan.target_weights["AAA"] == 0.1


def test_professional_adapter_enforces_max_positions_deterministically(monkeypatch) -> None:
    symbols = [f"S{index:02d}" for index in range(9)]
    bars = [row for symbol in symbols for row in _bars(symbol)]
    close_by_symbol = {
        symbol: float(next(row for row in reversed(bars) if row["symbol"] == symbol)["close"])
        for symbol in symbols
    }
    monkeypatch.setattr(
        "quantpilot.packages.core.signals.service.build_multi_factor_score",
        lambda signal, **_: _fixed_multifactor(signal.symbol),
    )

    signal_set = generate_provider_bound_signals(
        load_strategy_recipe("pullback_trend_v2"),
        FakeOHLCVProvider(bars),
        quote_provider=StaticQuoteProvider(
            {symbol: _quote(symbol, close_by_symbol[symbol]) for symbol in symbols}
        ),
        policy=UserPolicy(max_positions=8),
        securities=[_security(symbol) for symbol in symbols],
        portfolio_snapshot=_snapshot(),
        evaluated_at=EVALUATED_AT,
    )

    buy_symbols = [
        signal.symbol for signal in signal_set.signals if signal.action == SignalAction.buy_ready
    ]
    capped = [signal for signal in signal_set.signals if "max_positions_cap" in signal.reason_codes]
    assert buy_symbols == symbols[:8]
    assert len(capped) == 1
    assert capped[0].symbol == "S08"
    assert capped[0].action == SignalAction.watch
    assert capped[0].target_weight_hint == 0.0
