from __future__ import annotations

from datetime import date, timedelta
from statistics import pstdev
from typing import Any

import pytest

from quantpilot.packages.core.schemas import SignalAction, StrategyRecipe
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.technical.indicators import _rsi
from quantpilot.services.strategy_design import rule_engine
from quantpilot.services.strategy_design.rule_engine import (
    GRAMMAR_HELP,
    Binary,
    Boolean,
    Name,
    Number,
    RuleReferenceError,
    RuleSyntaxError,
    Unary,
    evaluate,
    generate_signals,
    infer_type,
    parse,
    validate_recipe_rules,
)

# --------------------------------------------------------------------------- fixtures


def _bar(
    symbol: str,
    session: date,
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    volume: float = 10_000,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "date": session.isoformat(),
        "open": close,
        "high": close * 1.02 if high is None else high,
        "low": close * 0.98 if low is None else low,
        "close": close,
        "volume": volume,
    }


def _series(symbol: str, closes: list[float], *, start: date = date(2026, 1, 1)) -> list[dict[str, Any]]:
    return [_bar(symbol, start + timedelta(days=offset), close) for offset, close in enumerate(closes)]


def _thirty_closes() -> list[float]:
    # Deterministic, mixed up/down moves so RSI has both gains and losses.
    return [100.0 + ((index * 7) % 11) - 5 + index * 0.5 for index in range(30)]


def _recipe(
    features: list[dict[str, Any]],
    entry: list[str],
    exit_: list[str],
    *,
    position_sizing: dict[str, Any] | None = None,
) -> StrategyRecipe:
    return StrategyRecipe(
        strategy_id="llm_rule_test",
        version="0.1",
        features=features,
        entry_rules=entry,
        exit_rules=exit_,
        position_sizing=position_sizing or {"method": "capped_target_weight", "max_target_weight": 0.10},
        risk_rules=["limit orders only"],
        rebalance="daily",
    )


def _eval(text: str, bars: list[dict[str, Any]], features: dict[str, Any] | None = None) -> list[Any]:
    return evaluate(parse(text), bars, features or {})


# --------------------------------------------------------------------------- parser


def test_parser_precedence_and_parentheses() -> None:
    bars = _series("AAA", [1.0])
    assert _eval("1 + 2 * 3 == 7", bars) == [True]
    assert _eval("(1 + 2) * 3 == 9", bars) == [True]
    assert _eval("-2 * 3 == -6", bars) == [True]
    assert _eval("8 / 2 / 2 == 2", bars) == [True]
    assert _eval("1 - 2 - 3 == -4", bars) == [True]

    tree = parse("1 + 2 * 3")
    assert isinstance(tree, Binary) and tree.op == "+"
    assert isinstance(tree.right, Binary) and tree.right.op == "*"


def test_parser_not_and_or() -> None:
    bars = _series("AAA", [1.0])
    assert _eval("not false", bars) == [True]
    assert _eval("not 1 > 2", bars) == [True]  # not binds looser than comparison
    assert _eval("true and false or true", bars) == [True]  # and binds tighter than or
    assert _eval("true and (false or false)", bars) == [False]
    assert _eval("not true or true", bars) == [True]
    tree = parse("not close > 1")
    assert isinstance(tree, Unary) and tree.op == "not" and isinstance(tree.operand, Binary)


def test_parser_crosses_above_and_below() -> None:
    bars = _series("AAA", [10.0, 10.0, 12.0, 9.0, 9.0])
    assert _eval("close crosses_above 10", bars) == [False, False, True, False, False]
    assert _eval("close crosses_below 10", bars) == [False, False, False, True, False]
    # equal-then-above counts as a cross; above-then-above does not.
    assert _eval("close crosses_above 10", _series("AAA", [11.0, 12.0])) == [False, False]


def test_parser_phrase_sugar() -> None:
    bars = _series("AAA", [1.0, 2.0])
    features = {"trend": parse("close > 1.5")}
    assert _eval("trend is true", bars, features) == [False, True]
    assert _eval("trend is false", bars, features) == [True, False]
    assert _eval("trend is true and close > 0", bars, features) == [False, True]
    with pytest.raises(RuleSyntaxError):
        parse("trend is maybe")


def test_parser_errors_carry_position() -> None:
    with pytest.raises(RuleSyntaxError, match="sma\\(\\) takes 2 argument"):
        parse("sma(close)")
    with pytest.raises(RuleSyntaxError, match="position 7"):
        parse("close +")
    with pytest.raises(RuleSyntaxError, match="unexpected character"):
        parse("close $ 1")
    with pytest.raises(RuleSyntaxError, match="window must be a positive integer"):
        parse("sma(close, 2.5)")
    with pytest.raises(RuleSyntaxError):
        parse("1 < 2 < 3")  # chained comparisons are not supported
    with pytest.raises(RuleSyntaxError):
        parse("")
    with pytest.raises(RuleReferenceError, match="unknown function 'vwap'"):
        parse("vwap(close, 3)")


def test_unknown_identifier_is_a_reference_error() -> None:
    bars = _series("AAA", [1.0])
    with pytest.raises(RuleReferenceError, match="unknown identifier 'foo'"):
        evaluate(parse("foo > 1"), bars, {})
    with pytest.raises(RuleReferenceError, match="unknown identifier 'foo'"):
        infer_type(parse("foo > 1"), {})
    assert issubclass(RuleReferenceError, RuleSyntaxError)


# --------------------------------------------------------------------------- indicators


def test_sma_matches_hand_computation() -> None:
    closes = _thirty_closes()
    series = _eval("sma(close, 5)", _series("AAA", closes))
    assert series[-1] == pytest.approx(sum(closes[-5:]) / 5)
    assert series[4] == pytest.approx(sum(closes[:5]) / 5)


def test_rsi_matches_indicators_convention() -> None:
    closes = _thirty_closes()
    series = _eval("rsi(close, 14)", _series("AAA", closes))
    assert series[-1] == pytest.approx(_rsi(closes, 14))
    assert series[20] == pytest.approx(_rsi(closes[:21], 14))
    assert series[13] is None  # 14 changes need 15 values
    assert series[14] is not None
    # flat -> 50, only gains -> 100
    assert _eval("rsi(close, 3)", _series("AAA", [5.0] * 5))[-1] == 50.0
    assert _eval("rsi(close, 3)", _series("AAA", [1.0, 2.0, 3.0, 4.0]))[-1] == 100.0


def test_ret_highest_lowest_lag_stdev() -> None:
    closes = _thirty_closes()
    bars = _series("AAA", closes)
    highs = [bar["high"] for bar in bars]
    assert _eval("ret(close, 5)", bars)[-1] == pytest.approx(closes[-1] / closes[-6] - 1)
    assert _eval("ret(close, 5)", bars)[4] is None
    assert _eval("highest(high, 10)", bars)[-1] == pytest.approx(max(highs[-10:]))
    assert _eval("highest(high, 10)", bars)[8] is None
    assert _eval("lowest(low, 10)", bars)[-1] == pytest.approx(min(bar["low"] for bar in bars[-10:]))
    assert _eval("lag(close, 3)", bars)[-1] == closes[-4]
    assert _eval("lag(close, 3)", bars)[2] is None
    assert _eval("stdev(close, 7)", bars)[-1] == pytest.approx(pstdev(closes[-7:]))
    assert _eval("abs(0 - 3) == 3 and max(1, 2) == 2 and min(1, 2) == 1", bars)[-1] is True


def test_ema_seeds_with_sma_then_smooths() -> None:
    closes = [10.0, 12.0, 14.0, 20.0, 16.0]
    series = _eval("ema(close, 3)", _series("AAA", closes))
    assert series[0] is None and series[1] is None
    seed = sum(closes[:3]) / 3
    assert series[2] == pytest.approx(seed)
    alpha = 2 / (3 + 1)
    third = alpha * closes[3] + (1 - alpha) * seed
    assert series[3] == pytest.approx(third)
    assert series[4] == pytest.approx(alpha * closes[4] + (1 - alpha) * third)


def test_atr_uses_previous_close() -> None:
    start = date(2026, 1, 1)
    bars = [
        _bar("AAA", start, 9.0, high=10.0, low=8.0),
        _bar("AAA", start + timedelta(days=1), 11.0, high=12.0, low=9.0),  # TR = max(3, |12-9|, |9-9|) = 3
        _bar("AAA", start + timedelta(days=2), 8.0, high=11.0, low=7.0),  # TR = max(4, |11-11|, |7-11|) = 4
    ]
    assert _eval("atr(1)", bars) == [None, 3.0, 4.0]
    assert _eval("atr(2)", bars) == [None, None, 3.5]


def test_nested_expressions_evaluate_as_series() -> None:
    closes = _thirty_closes()
    bars = _series("AAA", closes, )
    volumes = [bar["volume"] for bar in bars]
    assert _eval("sma(volume, 20)", bars)[-1] == pytest.approx(sum(volumes[-20:]) / 20)
    ratio = [closes[t] / (sum(closes[t - 4 : t + 1]) / 5) if t >= 4 else None for t in range(30)]
    expected = sum(ratio[-10:]) / 10  # type: ignore[arg-type]
    assert _eval("sma(close / sma(close, 5), 10)", bars)[-1] == pytest.approx(expected)
    assert _eval("sma(close / sma(close, 5), 10)", bars)[12] is None  # 4 + 9 bars of warmup


# --------------------------------------------------------------------------- None propagation


def test_none_propagation() -> None:
    closes = _thirty_closes()
    bars = _series("AAA", closes)
    sma5 = _eval("sma(close, 5)", bars)
    assert sma5[:4] == [None] * 4
    assert sma5[4] is not None
    comparison = _eval("close > sma(close, 5)", bars)
    assert comparison[:4] == [False] * 4
    assert all(isinstance(value, bool) for value in comparison)
    arithmetic = _eval("sma(close, 5) + 1", bars)
    assert arithmetic[:4] == [None] * 4
    assert _eval("close / 0", bars)[0] is None
    assert _eval("close / 0 > 1", bars)[0] is False
    assert _eval("close / (close - close)", bars)[0] is None
    # Missing booleans never make a rule fire: not/and/or propagate the gap.
    assert _eval("not (sma(close, 5) > 0 and close / 0 > 1)", bars)[0] is True
    features = {"flag": parse("sma(close, 5) / 0 > 1")}
    assert _eval("flag", bars, features)[0] is False


# --------------------------------------------------------------------------- validate_recipe_rules


def _v2_like_features() -> list[dict[str, Any]]:
    return [
        {"name": "trend_filter", "formula": "close > sma(close, 120)", "lookback_days": 120, "source_citation": "x"},
        {"name": "pullback_rsi", "formula": "rsi(close, 14)", "lookback_days": 14, "source_citation": "x"},
        {"name": "volume_confirm", "formula": "volume / sma(volume, 20)", "lookback_days": 20, "source_citation": "x"},
    ]


def test_validate_accepts_grammar_conformant_v2_like_recipe() -> None:
    recipe = _recipe(
        _v2_like_features(),
        ["trend_filter is true", "pullback_rsi crosses_above 35", "volume_confirm > 1.05"],
        ["close < sma(close, 20) * 0.94", "pullback_rsi > 72"],
        position_sizing={"method": "capped_score_weight", "max_target_weight": 0.15, "min_rebalance_band": 0.01},
    )
    assert validate_recipe_rules(recipe) == []


def test_validate_rejects_shipped_v2_prose_rules() -> None:
    recipe = load_strategy_recipe("pullback_trend_v2")
    errors = validate_recipe_rules(recipe)
    assert errors
    assert any("pullback_rsi recovers from oversold zone" in error for error in errors)


def test_validate_reports_type_and_structure_errors() -> None:
    bool_feature = [{"name": "trend", "formula": "close > sma(close, 3)", "lookback_days": 3}]
    assert any(
        "arithmetic" in error
        for error in validate_recipe_rules(_recipe(bool_feature, ["trend + 1 > 0"], ["close < 1"]))
    )
    assert any(
        "comparison" in error
        for error in validate_recipe_rules(_recipe(bool_feature, ["trend == trend"], ["close < 1"]))
    )
    assert any(
        "not a boolean" in error
        for error in validate_recipe_rules(_recipe([], ["close + 1"], ["close < 1"]))
    )
    assert any(
        "unknown identifier 'foo'" in error
        for error in validate_recipe_rules(_recipe([], ["foo > 1"], ["close < 1"]))
    )
    assert any(
        "defined twice" in error
        for error in validate_recipe_rules(
            _recipe(bool_feature + bool_feature, ["trend"], ["close < 1"])
        )
    )
    assert any(
        "does not parse" in error
        for error in validate_recipe_rules(_recipe([], ["close +"], ["close < 1"]))
    )
    assert any(
        "formula" in error
        for error in validate_recipe_rules(
            _recipe([{"name": "broken", "formula": "sma(close)"}], ["close > 1"], ["close < 1"])
        )
    )
    assert any("exit_rules is empty" in error for error in validate_recipe_rules(_recipe([], ["close > 1"], [])))
    assert any("entry_rules is empty" in error for error in validate_recipe_rules(_recipe([], [], ["close < 1"])))


def test_validate_requires_max_target_weight() -> None:
    missing = validate_recipe_rules(
        _recipe([], ["close > 1"], ["close < 1"], position_sizing={"method": "capped_target_weight"})
    )
    assert any("max_target_weight is missing" in error for error in missing)
    for bad in (0, -0.1, 1.5):
        errors = validate_recipe_rules(
            _recipe(
                [],
                ["close > 1"],
                ["close < 1"],
                position_sizing={"method": "capped_target_weight", "max_target_weight": bad},
            )
        )
        assert any("max_target_weight" in error for error in errors), bad


def test_validate_boolean_feature_counts_as_boolean_rule() -> None:
    recipe = _recipe(
        [{"name": "trend", "formula": "close > sma(close, 3)", "lookback_days": 3}],
        ["trend"],
        ["not trend"],
    )
    assert validate_recipe_rules(recipe) == []


# --------------------------------------------------------------------------- generate_signals


def _two_symbol_history() -> list[dict[str, Any]]:
    trend_up = [100.0 + index * 1.5 for index in range(15)] + [121.0 - index * 3.0 for index in range(1, 7)]
    chop = [100.0 if index % 2 == 0 else 90.0 for index in range(21)]
    rows_a = _series("aaa", trend_up)
    rows_b = _series("bbb", chop)
    history: list[dict[str, Any]] = []
    for row_a, row_b in zip(rows_a, rows_b):  # interleave per session
        history.extend([row_a, row_b])
    return history


def _trend_recipe(max_target_weight: float = 0.10) -> StrategyRecipe:
    return _recipe(
        [{"name": "trend", "formula": "close > sma(close, 3)", "lookback_days": 3, "source_citation": "x"}],
        ["trend is true"],
        ["close < sma(close, 3)"],
        position_sizing={"method": "capped_target_weight", "max_target_weight": max_target_weight},
    )


def test_generate_signals_state_machine() -> None:
    history = _two_symbol_history()
    signals = generate_signals(_trend_recipe(0.10), history)

    assert signals, "expected at least one signal"
    assert signals == sorted(signals, key=lambda s: (s.signal_date, s.symbol))
    assert {signal.symbol for signal in signals} == {"AAA", "BBB"}
    assert all(signal.source == "strategy_rule_engine" for signal in signals)
    assert all(signal.strength == 1.0 for signal in signals)

    first = signals[0]
    assert first.action == SignalAction.buy_ready
    assert first.target_weight_hint == min(0.15, 0.10)
    assert first.reason == "entry: all rules true"
    assert first.signal_date >= date(2026, 1, 1) + timedelta(days=3)  # warmup = lookback 3 + 1

    for symbol in ("AAA", "BBB"):
        actions = [signal.action for signal in signals if signal.symbol == symbol]
        assert actions[0] == SignalAction.buy_ready
        assert all(actions[i] != actions[i - 1] for i in range(1, len(actions))), "no second buy while held"
        assert SignalAction.exit in actions

    aaa = [signal for signal in signals if signal.symbol == "AAA"]
    assert aaa[1].action == SignalAction.exit
    assert aaa[1].reason == "exit: close < sma(close, 3)"
    assert aaa[1].target_weight_hint is None
    # The trend series exits once it turns down; the buy came at the first post-warmup bar.
    assert aaa[0].signal_date == date(2026, 1, 4)
    assert aaa[1].signal_date > date(2026, 1, 15)


def test_generate_signals_caps_target_weight_and_limits() -> None:
    history = _two_symbol_history()
    signals = generate_signals(_trend_recipe(0.30), history, max_position_weight=0.15, limit_buffer_bps=50)
    buys = [signal for signal in signals if signal.action == SignalAction.buy_ready]
    exits = [signal for signal in signals if signal.action == SignalAction.exit]
    assert buys and exits
    assert all(signal.target_weight_hint == 0.15 for signal in buys)
    by_key = {(row["symbol"].upper(), row["date"]): row["close"] for row in history}
    for signal in buys:
        assert signal.limit_price == pytest.approx(by_key[(signal.symbol, signal.signal_date.isoformat())] * 1.005)
    for signal in exits:
        assert signal.limit_price == pytest.approx(by_key[(signal.symbol, signal.signal_date.isoformat())] * 0.995)
    assert all(signal.limit_price is None for signal in generate_signals(_trend_recipe(0.30), history))


def test_generate_signals_exit_wins_and_initial_positions_are_held() -> None:
    recipe = _recipe([], ["true"], ["close < 95"])
    history = _series("AAA", [100.0, 100.0, 90.0, 100.0])
    signals = generate_signals(recipe, history, warmup_bars=1)
    assert [signal.action for signal in signals] == [
        SignalAction.buy_ready,
        SignalAction.exit,  # entry and exit both true on bar 2; exit wins
        SignalAction.buy_ready,
    ]
    assert [signal.signal_date.day for signal in signals] == [1, 3, 4]

    held = generate_signals(recipe, history, warmup_bars=1, initial_positions={"aaa": 0.1})
    assert [signal.action for signal in held] == [SignalAction.exit, SignalAction.buy_ready]
    assert held[0].signal_date.day == 3


def test_generate_signals_default_warmup_and_invalid_recipe() -> None:
    recipe = _recipe([], ["true"], ["false"])
    history = _series("AAA", [100.0] * 25)
    signals = generate_signals(recipe, history)  # no lookbacks -> warmup 20
    assert signals[0].signal_date == date(2026, 1, 20)

    recipe_lb = _recipe([{"name": "f", "formula": "sma(close, 5)", "lookback_days": 5}], ["f > 0"], ["false"])
    assert generate_signals(recipe_lb, history)[0].signal_date == date(2026, 1, 6)  # 5 + 1

    with pytest.raises(ValueError, match="not machine-checkable"):
        generate_signals(load_strategy_recipe("pullback_trend_v2"), history)
    with pytest.raises(ValueError):
        generate_signals(recipe, history, warmup_bars=0)


def test_generate_signals_has_no_lookahead() -> None:
    history = _two_symbol_history()
    recipe = _recipe(
        [
            {"name": "trend", "formula": "close > sma(close, 3)", "lookback_days": 3},
            {"name": "fast_rsi", "formula": "rsi(close, 4)", "lookback_days": 4},
        ],
        ["trend is true", "fast_rsi > 40", "close crosses_above ema(close, 2)"],
        ["close < sma(close, 3)", "ret(close, 2) < 0 - 0.05"],
    )
    full = generate_signals(recipe, history)
    assert full, "the regression needs a non-empty signal set"
    for k in (10, 14, 20, 28, 34, 40):
        partial = generate_signals(recipe, history[:k])
        cutoff = date.fromisoformat(history[k - 1]["date"])
        expected = [signal for signal in full if signal.signal_date <= cutoff]
        assert partial == expected, f"lookahead detected at k={k}"


def test_grammar_help_mentions_every_function() -> None:
    assert len(GRAMMAR_HELP.strip().splitlines()) <= 25
    for name in rule_engine._FUNCTIONS:
        assert f"{name}(" in GRAMMAR_HELP, name
    for keyword in ("crosses_above", "crosses_below", "is true", "is false", "and", "or", "not"):
        assert keyword in GRAMMAR_HELP


def test_ast_nodes_are_dataclasses() -> None:
    tree = parse("close > 1 and true")
    assert isinstance(tree, Binary)
    assert isinstance(tree.left.left, Name) and isinstance(tree.left.right, Number)  # type: ignore[attr-defined]
    assert isinstance(tree.right, Boolean)
    assert tree == parse("close > 1 and true")  # frozen dataclasses compare structurally
