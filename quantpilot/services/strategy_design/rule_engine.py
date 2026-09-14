"""Machine-checkable rule engine for LLM-authored ``StrategyRecipe`` YAML.

A recipe's ``features`` formulas and ``entry_rules`` / ``exit_rules`` are
written in a small expression language (see ``GRAMMAR_HELP``). This module
tokenizes and parses that language into an AST, evaluates every expression as
a *series* over a symbol's bar history (one value per bar, computed strictly
from bars ``0..t``), validates a recipe statically, and turns a recipe into
``BacktestSignal`` inputs for ``run_backtest`` with the same per-session /
held-state structure as ``quantpilot.packages.core.backtest.replay``.

No lookahead: every function only looks backward, so precomputing a series over
the full history is equivalent to bar-by-bar evaluation — appending future
rows can never change an earlier signal (enforced by a regression test).

Research-only: this module emits backtest inputs. It never touches brokers,
order plans, repositories, or strategy promotion state. Pure standard library
plus pydantic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from statistics import mean, pstdev
from typing import Any, Literal

from quantpilot.packages.core.backtest.schemas import BacktestSignal
from quantpilot.packages.core.schemas import SignalAction, StrategyRecipe

GRAMMAR_HELP = """Rule expression grammar (one boolean expression per entry/exit rule):
- Literals: numbers (10, 0.94) and booleans (true, false).
- Identifiers: open, high, low, close, volume, or a feature defined EARLIER in `features`.
- Functions (all windows are positive integer literals; only bars <= current bar are used):
    sma(x, n)      simple moving average of x over the last n bars
    ema(x, n)      exponential MA, seeded with sma of the first n bars, alpha = 2/(n+1)
    rsi(x, n)      RSI over the last n changes (50 when flat, 100 when no losses)
    atr(n)         simple mean of the true range over the last n bars (uses previous close)
    ret(x, n)      x_t / x_{t-n} - 1
    highest(x, n)  highest x over the last n bars including the current bar
    lowest(x, n)   lowest x over the last n bars including the current bar
    stdev(x, n)    population standard deviation of x over the last n bars
    lag(x, n)      value of x n bars ago
    abs(x), max(a, b), min(a, b)
  `x`, `a`, `b` may be any numeric expression, e.g. sma(close / sma(close, 5), 10).
- Operators, highest precedence first: unary -, then * /, then + -,
  then comparisons > < >= <= == != and `a crosses_above b` / `a crosses_below b`
  (a_{t-1} <= b_{t-1} and a_t > b_t, resp. a_{t-1} >= b_{t-1} and a_t < b_t),
  then not, then and, then or. Parentheses group.
- Sugar: `<feature> is true` means `<feature>`; `<feature> is false` means `not <feature>`.
- Missing history for a window or division by zero yields a missing value; any comparison
  involving a missing value is false, so a rule can never fire on missing data.
- Numbers and booleans do not mix: no arithmetic on booleans, no comparison of two booleans,
  and every rule must be boolean (a comparison, and/or/not, true/false, or a boolean feature).
"""

PRICE_COLUMNS = ("open", "high", "low", "close", "volume")

ValueType = Literal["number", "bool"]


class RuleSyntaxError(ValueError):
    """Bad rule syntax; ``position`` is the 0-based character offset in the text."""

    def __init__(self, message: str, position: int | None = None) -> None:
        self.position = position
        suffix = f" at position {position}" if position is not None else ""
        super().__init__(f"{message}{suffix}")


class RuleReferenceError(RuleSyntaxError):
    """Unknown identifier or function."""


class RuleTypeError(RuleSyntaxError):
    """Number/bool mismatch found by the static type check."""


# --------------------------------------------------------------------------- AST


@dataclass(frozen=True)
class Expr:
    pos: int


@dataclass(frozen=True)
class Number(Expr):
    value: float


@dataclass(frozen=True)
class Boolean(Expr):
    value: bool


@dataclass(frozen=True)
class Name(Expr):
    name: str


@dataclass(frozen=True)
class Call(Expr):
    func: str
    args: tuple[Expr, ...]


@dataclass(frozen=True)
class Unary(Expr):
    op: str  # "-" or "not"
    operand: Expr


@dataclass(frozen=True)
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


# function name -> (number of series arguments, takes trailing integer window)
_FUNCTIONS: dict[str, tuple[int, bool]] = {
    "sma": (1, True),
    "ema": (1, True),
    "rsi": (1, True),
    "atr": (0, True),
    "ret": (1, True),
    "highest": (1, True),
    "lowest": (1, True),
    "stdev": (1, True),
    "lag": (1, True),
    "abs": (1, False),
    "max": (2, False),
    "min": (2, False),
}

_COMPARISON_OPS = {">", "<", ">=", "<=", "==", "!="}
_CROSS_OPS = {"crosses_above", "crosses_below"}
_ARITHMETIC_OPS = {"+", "-", "*", "/"}
_KEYWORDS = {"and", "or", "not", "true", "false", "is"} | _CROSS_OPS


# --------------------------------------------------------------------------- tokenizer


@dataclass(frozen=True)
class _Token:
    kind: str  # "number" | "ident" | "op" | "eof"
    text: str
    pos: int


_TOKEN_RE = re.compile(
    r"\s*(?:(?P<number>\d+(?:\.\d+)?)|(?P<ident>[A-Za-z_]\w*)|(?P<op>>=|<=|==|!=|[-+*/()<>,]))"
)


def tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    length = len(text)
    while index < length:
        match = _TOKEN_RE.match(text, index)
        if match is None or match.end() == index:
            stripped = text[index:].lstrip()
            if not stripped:
                break
            bad_pos = length - len(stripped)
            raise RuleSyntaxError(f"unexpected character {text[bad_pos]!r}", bad_pos)
        kind = match.lastgroup or "op"
        value = match.group(kind)
        tokens.append(_Token(kind, value, match.start(kind)))
        index = match.end()
    tokens.append(_Token("eof", "", length))
    return tokens


# --------------------------------------------------------------------------- parser


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = tokenize(text)
        self.index = 0

    # helpers
    def _peek(self) -> _Token:
        return self.tokens[self.index]

    def _advance(self) -> _Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _peek_is(self, kind: str, text: str | None = None) -> bool:
        token = self._peek()
        return token.kind == kind and (text is None or token.text == text)

    def _expect(self, kind: str, text: str) -> _Token:
        token = self._peek()
        if token.kind != kind or token.text != text:
            raise RuleSyntaxError(f"expected {text!r} but found {self._describe(token)}", token.pos)
        return self._advance()

    @staticmethod
    def _describe(token: _Token) -> str:
        return "end of input" if token.kind == "eof" else repr(token.text)

    # grammar
    def parse(self) -> Expr:
        expr = self._parse_or()
        token = self._peek()
        if token.kind != "eof":
            raise RuleSyntaxError(f"unexpected {self._describe(token)}", token.pos)
        return expr

    def _parse_or(self) -> Expr:
        left = self._parse_and()
        while self._peek_is("ident", "or"):
            token = self._advance()
            left = Binary(token.pos, "or", left, self._parse_and())
        return left

    def _parse_and(self) -> Expr:
        left = self._parse_not()
        while self._peek_is("ident", "and"):
            token = self._advance()
            left = Binary(token.pos, "and", left, self._parse_not())
        return left

    def _parse_not(self) -> Expr:
        if self._peek_is("ident", "not"):
            token = self._advance()
            return Unary(token.pos, "not", self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> Expr:
        left = self._parse_additive()
        token = self._peek()
        if (token.kind == "op" and token.text in _COMPARISON_OPS) or (
            token.kind == "ident" and token.text in _CROSS_OPS
        ):
            self._advance()
            right = self._parse_additive()
            return Binary(token.pos, token.text, left, right)
        return left

    def _parse_additive(self) -> Expr:
        left = self._parse_term()
        while self._peek_is("op", "+") or self._peek_is("op", "-"):
            token = self._advance()
            left = Binary(token.pos, token.text, left, self._parse_term())
        return left

    def _parse_term(self) -> Expr:
        left = self._parse_unary()
        while self._peek_is("op", "*") or self._peek_is("op", "/"):
            token = self._advance()
            left = Binary(token.pos, token.text, left, self._parse_unary())
        return left

    def _parse_unary(self) -> Expr:
        if self._peek_is("op", "-"):
            token = self._advance()
            return Unary(token.pos, "-", self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> Expr:
        token = self._advance()
        if token.kind == "number":
            return Number(token.pos, float(token.text))
        if token.kind == "op" and token.text == "(":
            inner = self._parse_or()
            self._expect("op", ")")
            return inner
        if token.kind == "ident":
            if token.text in {"true", "false"}:
                return Boolean(token.pos, token.text == "true")
            if token.text in _KEYWORDS:
                raise RuleSyntaxError(f"unexpected keyword {token.text!r}", token.pos)
            if self._peek_is("op", "("):
                return self._parse_call(token)
            name = Name(token.pos, token.text)
            if self._peek_is("ident", "is"):  # "<feature> is true|false" sugar
                self._advance()
                flag = self._advance()
                if flag.kind != "ident" or flag.text not in {"true", "false"}:
                    raise RuleSyntaxError(
                        f"expected 'true' or 'false' after 'is' but found {self._describe(flag)}",
                        flag.pos,
                    )
                return name if flag.text == "true" else Unary(flag.pos, "not", name)
            return name
        raise RuleSyntaxError(f"unexpected {self._describe(token)}", token.pos)

    def _parse_call(self, name_token: _Token) -> Expr:
        func = name_token.text
        if func not in _FUNCTIONS:
            raise RuleReferenceError(f"unknown function {func!r}", name_token.pos)
        self._expect("op", "(")
        args: list[Expr] = []
        if not self._peek_is("op", ")"):
            args.append(self._parse_or())
            while self._peek_is("op", ","):
                self._advance()
                args.append(self._parse_or())
        self._expect("op", ")")

        series_count, has_window = _FUNCTIONS[func]
        expected = series_count + (1 if has_window else 0)
        if len(args) != expected:
            raise RuleSyntaxError(
                f"{func}() takes {expected} argument(s) but {len(args)} were given", name_token.pos
            )
        if has_window:
            window = args[-1]
            if not isinstance(window, Number) or window.value < 1 or window.value != int(window.value):
                raise RuleSyntaxError(
                    f"{func}() window must be a positive integer literal", window.pos
                )
        return Call(name_token.pos, func, tuple(args))


def parse(text: str) -> Expr:
    """Parse a feature formula or rule string into an AST."""
    if not isinstance(text, str) or not text.strip():
        raise RuleSyntaxError("empty expression", 0)
    return _Parser(text).parse()


# --------------------------------------------------------------------------- static typing


def infer_type(expr: Expr, features: dict[str, Expr]) -> ValueType:
    """Return "number" or "bool"; raise RuleReferenceError / RuleTypeError."""
    return _infer_type(expr, features, ())


def _infer_type(expr: Expr, features: dict[str, Expr], stack: tuple[str, ...]) -> ValueType:
    if isinstance(expr, Number):
        return "number"
    if isinstance(expr, Boolean):
        return "bool"
    if isinstance(expr, Name):
        if expr.name in PRICE_COLUMNS:
            return "number"
        if expr.name in features:
            if expr.name in stack:
                raise RuleReferenceError(f"feature {expr.name!r} refers to itself", expr.pos)
            return _infer_type(features[expr.name], features, stack + (expr.name,))
        raise RuleReferenceError(f"unknown identifier {expr.name!r}", expr.pos)
    if isinstance(expr, Call):
        series_count, _ = _FUNCTIONS[expr.func]
        for arg in expr.args[:series_count]:
            if _infer_type(arg, features, stack) != "number":
                raise RuleTypeError(f"{expr.func}() requires numeric arguments", arg.pos)
        return "number"
    if isinstance(expr, Unary):
        operand = _infer_type(expr.operand, features, stack)
        if expr.op == "not":
            if operand != "bool":
                raise RuleTypeError("'not' requires a boolean operand", expr.pos)
            return "bool"
        if operand != "number":
            raise RuleTypeError("unary '-' requires a numeric operand", expr.pos)
        return "number"
    if isinstance(expr, Binary):
        left = _infer_type(expr.left, features, stack)
        right = _infer_type(expr.right, features, stack)
        if expr.op in _ARITHMETIC_OPS:
            if left != "number" or right != "number":
                raise RuleTypeError(f"arithmetic {expr.op!r} on a boolean value", expr.pos)
            return "number"
        if expr.op in _COMPARISON_OPS or expr.op in _CROSS_OPS:
            if left != "number" or right != "number":
                raise RuleTypeError(f"comparison {expr.op!r} requires numeric operands", expr.pos)
            return "bool"
        if left != "bool" or right != "bool":
            raise RuleTypeError(f"{expr.op!r} requires boolean operands", expr.pos)
        return "bool"
    raise RuleSyntaxError(f"unsupported expression node {type(expr).__name__}", expr.pos)


# --------------------------------------------------------------------------- series evaluation

Value = float | bool | None


def _column(bars: list[dict[str, Any]], name: str) -> list[float | None]:
    out: list[float | None] = []
    for bar in bars:
        raw = bar.get(name)
        if raw is None:
            out.append(None)
            continue
        try:
            out.append(float(raw))
        except (TypeError, ValueError):
            out.append(None)
    return out


def _window(values: list[Value], end: int, n: int) -> list[float] | None:
    """Values ``end-n+1..end`` as floats, or None when short or containing None."""
    start = end - n + 1
    if start < 0:
        return None
    window = values[start : end + 1]
    if any(value is None for value in window):
        return None
    return [float(value) for value in window]  # type: ignore[arg-type]


def _rsi_from_window(values: list[float]) -> float:
    # Same convention as quantpilot.packages.core.technical.indicators._rsi:
    # simple mean of gains / losses over the last n changes.
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [change for change in changes if change > 0]
    losses = [-change for change in changes if change < 0]
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


class _SeriesEvaluator:
    """Evaluates expressions as series over one symbol's bars, caching features."""

    def __init__(self, bars: list[dict[str, Any]], features: dict[str, Expr]) -> None:
        self.size = len(bars)
        self.columns = {name: _column(bars, name) for name in PRICE_COLUMNS}
        self.features = features
        self._cache: dict[str, list[Value]] = {}
        self._resolving: set[str] = set()

    def series(self, expr: Expr) -> list[Value]:
        if isinstance(expr, Number):
            return [expr.value] * self.size
        if isinstance(expr, Boolean):
            return [expr.value] * self.size
        if isinstance(expr, Name):
            return self._name(expr)
        if isinstance(expr, Call):
            return self._call(expr)
        if isinstance(expr, Unary):
            return self._unary(expr)
        if isinstance(expr, Binary):
            return self._binary(expr)
        raise RuleSyntaxError(f"unsupported expression node {type(expr).__name__}", expr.pos)

    def _name(self, expr: Name) -> list[Value]:
        if expr.name in PRICE_COLUMNS:
            return list(self.columns[expr.name])
        if expr.name not in self.features:
            raise RuleReferenceError(f"unknown identifier {expr.name!r}", expr.pos)
        cached = self._cache.get(expr.name)
        if cached is None:
            if expr.name in self._resolving:
                raise RuleReferenceError(f"feature {expr.name!r} refers to itself", expr.pos)
            self._resolving.add(expr.name)
            try:
                cached = self.series(self.features[expr.name])
            finally:
                self._resolving.discard(expr.name)
            self._cache[expr.name] = cached
        return list(cached)

    def _unary(self, expr: Unary) -> list[Value]:
        operand = self.series(expr.operand)
        if expr.op == "not":
            return [None if value is None else not value for value in operand]
        return [None if value is None else -float(value) for value in operand]

    def _binary(self, expr: Binary) -> list[Value]:
        op = expr.op
        left = self.series(expr.left)
        right = self.series(expr.right)
        if op in _CROSS_OPS:
            return self._cross(op, left, right)
        return [_apply_binary(op, a, b) for a, b in zip(left, right)]

    @staticmethod
    def _cross(op: str, left: list[Value], right: list[Value]) -> list[Value]:
        out: list[Value] = [False] * len(left)
        for index in range(1, len(left)):
            a0, b0, a1, b1 = left[index - 1], right[index - 1], left[index], right[index]
            if a0 is None or b0 is None or a1 is None or b1 is None:
                continue
            if op == "crosses_above":
                out[index] = a0 <= b0 and a1 > b1
            else:
                out[index] = a0 >= b0 and a1 < b1
        return out

    def _call(self, expr: Call) -> list[Value]:
        func = expr.func
        series_count, has_window = _FUNCTIONS[func]
        inputs = [self.series(arg) for arg in expr.args[:series_count]]
        n = int(expr.args[-1].value) if has_window else 0  # type: ignore[attr-defined]

        if func == "abs":
            return [None if v is None else abs(float(v)) for v in inputs[0]]
        if func in {"max", "min"}:
            pick = max if func == "max" else min
            return [
                None if a is None or b is None else pick(float(a), float(b))
                for a, b in zip(inputs[0], inputs[1])
            ]
        if func == "atr":
            return self._atr(n)

        x = inputs[0]
        if func == "sma":
            return [None if (w := _window(x, t, n)) is None else sum(w) / n for t in range(self.size)]
        if func == "stdev":
            return [None if (w := _window(x, t, n)) is None else pstdev(w) for t in range(self.size)]
        if func == "highest":
            return [None if (w := _window(x, t, n)) is None else max(w) for t in range(self.size)]
        if func == "lowest":
            return [None if (w := _window(x, t, n)) is None else min(w) for t in range(self.size)]
        if func == "rsi":
            # n changes need n + 1 values.
            return [
                None if (w := _window(x, t, n + 1)) is None else _rsi_from_window(w)
                for t in range(self.size)
            ]
        if func == "lag":
            return [None if t < n else x[t - n] for t in range(self.size)]
        if func == "ret":
            out: list[Value] = []
            for t in range(self.size):
                if t < n or x[t] is None or x[t - n] is None or float(x[t - n]) == 0:
                    out.append(None)
                else:
                    out.append(float(x[t]) / float(x[t - n]) - 1)
            return out
        if func == "ema":
            return self._ema(x, n)
        raise RuleReferenceError(f"unknown function {func!r}", expr.pos)

    def _ema(self, x: list[Value], n: int) -> list[Value]:
        alpha = 2.0 / (n + 1)
        out: list[Value] = []
        prev: float | None = None
        for t in range(self.size):
            value = x[t]
            if prev is None:
                window = _window(x, t, n)
                prev = None if window is None else sum(window) / n
            elif value is None:
                prev = None  # gap in the input: re-seed once n clean bars are available
            else:
                prev = alpha * float(value) + (1 - alpha) * prev
            out.append(prev)
        return out

    def _atr(self, n: int) -> list[Value]:
        highs, lows, closes = self.columns["high"], self.columns["low"], self.columns["close"]
        true_range: list[Value] = [None] * self.size  # bar 0 has no previous close
        for t in range(1, self.size):
            high, low, prev_close = highs[t], lows[t], closes[t - 1]
            if high is None or low is None or prev_close is None:
                continue
            true_range[t] = max(high - low, abs(high - prev_close), abs(low - prev_close))
        return [None if (w := _window(true_range, t, n)) is None else sum(w) / n for t in range(self.size)]


def _apply_binary(op: str, a: Value, b: Value) -> Value:
    if op == "and":
        if a is False or b is False:
            return False
        if a is None or b is None:
            return None
        return bool(a) and bool(b)
    if op == "or":
        if a is True or b is True:
            return True
        if a is None or b is None:
            return None
        return bool(a) or bool(b)
    if op in _COMPARISON_OPS:
        if a is None or b is None:
            return False
        fa, fb = float(a), float(b)
        if op == ">":
            return fa > fb
        if op == "<":
            return fa < fb
        if op == ">=":
            return fa >= fb
        if op == "<=":
            return fa <= fb
        if op == "==":
            return fa == fb
        return fa != fb
    # arithmetic
    if a is None or b is None:
        return None
    fa, fb = float(a), float(b)
    if op == "+":
        return fa + fb
    if op == "-":
        return fa - fb
    if op == "*":
        return fa * fb
    if fb == 0:
        return None
    return fa / fb


def evaluate(expr: Expr, bars: list[dict[str, Any]], features: dict[str, Expr]) -> list[Value]:
    """Evaluate ``expr`` over ``bars``; value ``t`` depends only on ``bars[0..t]``."""
    return _SeriesEvaluator(bars, features).series(expr)


# --------------------------------------------------------------------------- recipe validation


def _feature_exprs(recipe: StrategyRecipe) -> tuple[dict[str, Expr], list[str]]:
    """Parse features in declaration order; return (parsed features, errors)."""
    features: dict[str, Expr] = {}
    errors: list[str] = []
    for index, feature in enumerate(recipe.features):
        label = f"feature #{index + 1}"
        if not isinstance(feature, dict):
            errors.append(f"{label}: must be a mapping with name/formula")
            continue
        name = feature.get("name")
        formula = feature.get("formula")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_]\w*", name) or name in _KEYWORDS:
            errors.append(f"{label}: name {name!r} is not a valid identifier")
            continue
        label = f"feature {name!r}"
        if name in PRICE_COLUMNS or name in _FUNCTIONS:
            errors.append(f"{label}: name shadows a built-in identifier")
            continue
        if name in features:
            errors.append(f"{label}: defined twice")
            continue
        if not isinstance(formula, str) or not formula.strip():
            errors.append(f"{label}: formula is missing")
            continue
        try:
            expr = parse(formula)
            infer_type(expr, features)
        except RuleSyntaxError as exc:  # includes reference and type errors
            errors.append(f"{label}: formula {formula!r} is invalid: {exc}")
            continue
        features[name] = expr
    return features, errors


def _parse_rules(rules: list[str], kind: str, features: dict[str, Expr]) -> tuple[list[tuple[str, Expr]], list[str]]:
    parsed: list[tuple[str, Expr]] = []
    errors: list[str] = []
    if not rules:
        errors.append(f"{kind}_rules is empty")
        return parsed, errors
    for rule in rules:
        text = str(rule).strip()
        try:
            expr = parse(text)
            value_type = infer_type(expr, features)
        except RuleSyntaxError as exc:
            errors.append(f"{kind} rule {text!r} does not parse: {exc}")
            continue
        if value_type != "bool":
            errors.append(f"{kind} rule {text!r} is not a boolean expression")
            continue
        parsed.append((text, expr))
    return parsed, errors


def _max_target_weight(recipe: StrategyRecipe) -> tuple[float | None, str | None]:
    raw = recipe.position_sizing.get("max_target_weight") if isinstance(recipe.position_sizing, dict) else None
    if raw is None or isinstance(raw, bool):
        return None, "position_sizing.max_target_weight is missing"
    try:
        weight = float(raw)
    except (TypeError, ValueError):
        return None, f"position_sizing.max_target_weight {raw!r} is not a number"
    if not weight > 0 or weight > 1:
        return None, f"position_sizing.max_target_weight must be in (0, 1], got {weight}"
    return weight, None


def validate_recipe_rules(recipe: StrategyRecipe) -> list[str]:
    """Return human-readable problems that stop the recipe from being backtested (empty = ok)."""
    features, errors = _feature_exprs(recipe)
    _, entry_errors = _parse_rules(recipe.entry_rules, "entry", features)
    _, exit_errors = _parse_rules(recipe.exit_rules, "exit", features)
    errors.extend(entry_errors)
    errors.extend(exit_errors)
    _, weight_error = _max_target_weight(recipe)
    if weight_error:
        errors.append(weight_error)
    return errors


# --------------------------------------------------------------------------- signal generation


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _default_warmup(recipe: StrategyRecipe) -> int:
    lookbacks: list[int] = []
    for feature in recipe.features:
        if isinstance(feature, dict) and feature.get("lookback_days") is not None:
            try:
                lookbacks.append(int(feature["lookback_days"]))
            except (TypeError, ValueError):
                continue
    if not lookbacks:
        return 20
    return max(2, max(lookbacks)) + 1


def generate_signals(
    recipe: StrategyRecipe,
    price_history: list[dict[str, Any]],
    *,
    warmup_bars: int | None = None,
    max_position_weight: float = 0.15,
    initial_positions: dict[str, float] | None = None,
    limit_buffer_bps: float = 0.0,
) -> list[BacktestSignal]:
    """Turn a machine-checkable recipe into ``BacktestSignal`` inputs.

    Per symbol the features and rules are evaluated once as series over the
    sorted bar history (every function only looks backward, so this equals
    bar-by-bar evaluation). Sessions are then walked in date order with a
    held-state machine: not held and ALL entry rules true -> ``buy_ready``;
    held and ANY exit rule true -> ``exit`` (exit wins when both fire on the
    same bar). Nothing else is emitted. ``limit_buffer_bps`` behaves as in
    ``replay.replay_signals`` (buys up, sells down from the close).
    """
    if warmup_bars is None:
        warmup_bars = _default_warmup(recipe)
    if warmup_bars < 1:
        raise ValueError("warmup_bars must be at least 1")
    if limit_buffer_bps < 0:
        raise ValueError("limit_buffer_bps must be non-negative")

    errors = validate_recipe_rules(recipe)
    if errors:
        raise ValueError("recipe rules are not machine-checkable: " + "; ".join(errors))
    features, _ = _feature_exprs(recipe)
    entry_rules, _ = _parse_rules(recipe.entry_rules, "entry", features)
    exit_rules, _ = _parse_rules(recipe.exit_rules, "exit", features)
    max_target_weight, _ = _max_target_weight(recipe)
    assert max_target_weight is not None
    target_weight = min(float(max_position_weight), max_target_weight)

    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in price_history:
        symbol = str(row.get("symbol", row.get("ticker", ""))).strip().upper()
        if not symbol:
            raise ValueError("price history row is missing symbol/ticker")
        rows_by_symbol.setdefault(symbol, []).append(dict(row, symbol=symbol))
    for rows in rows_by_symbol.values():
        rows.sort(key=lambda item: _parse_date(item["date"]))

    # Precompute series per symbol (features cached inside the evaluator).
    entry_series: dict[str, list[list[Value]]] = {}
    exit_series: dict[str, list[list[Value]]] = {}
    index_by_date: dict[str, dict[date, int]] = {}
    for symbol, rows in rows_by_symbol.items():
        evaluator = _SeriesEvaluator(rows, features)
        entry_series[symbol] = [evaluator.series(expr) for _, expr in entry_rules]
        exit_series[symbol] = [evaluator.series(expr) for _, expr in exit_rules]
        index_by_date[symbol] = {_parse_date(row["date"]): index for index, row in enumerate(rows)}

    held: dict[str, float] = {
        symbol.strip().upper(): float(weight)
        for symbol, weight in (initial_positions or {}).items()
        if float(weight) > 0
    }

    trading_dates = sorted({session for dates in index_by_date.values() for session in dates})
    buffer = limit_buffer_bps / 10_000.0
    signals: list[BacktestSignal] = []
    for session in trading_dates:
        for symbol in sorted(rows_by_symbol):
            index = index_by_date[symbol].get(session)
            if index is None:
                continue  # symbol did not trade this session
            if index + 1 < warmup_bars:
                continue
            close = rows_by_symbol[symbol][index].get("close")
            action: SignalAction | None = None
            reason = ""
            if symbol in held:
                for (text, _), series in zip(exit_rules, exit_series[symbol]):
                    if series[index] is True:
                        action, reason = SignalAction.exit, f"exit: {text}"
                        break
            elif all(series[index] is True for series in entry_series[symbol]):
                action, reason = SignalAction.buy_ready, "entry: all rules true"
            if action is None:
                continue

            limit_price: float | None = None
            hint: float | None = None
            if action == SignalAction.buy_ready:
                held[symbol] = target_weight
                hint = target_weight
                if limit_buffer_bps > 0 and close is not None:
                    limit_price = round(float(close) * (1 + buffer), 6)
            else:
                held.pop(symbol, None)
                if limit_buffer_bps > 0 and close is not None:
                    limit_price = round(float(close) * (1 - buffer), 6)
            signals.append(
                BacktestSignal(
                    symbol=symbol,
                    signal_date=session,
                    action=action,
                    strength=1.0,
                    target_weight_hint=hint,
                    limit_price=limit_price,
                    reason=reason,
                    source="strategy_rule_engine",
                )
            )
    signals.sort(key=lambda signal: (signal.signal_date, signal.symbol))
    return signals


__all__ = [
    "GRAMMAR_HELP",
    "PRICE_COLUMNS",
    "Binary",
    "Boolean",
    "Call",
    "Expr",
    "Name",
    "Number",
    "RuleReferenceError",
    "RuleSyntaxError",
    "RuleTypeError",
    "Unary",
    "evaluate",
    "generate_signals",
    "infer_type",
    "parse",
    "tokenize",
    "validate_recipe_rules",
]
