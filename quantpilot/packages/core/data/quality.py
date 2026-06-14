from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable


IssueSeverity = Literal["info", "warning", "error"]


def _issue_severity(value: object) -> IssueSeverity:
    severity = str(value)
    if severity == "info":
        return "info"
    if severity == "error":
        return "error"
    return "warning"


class TradingSessionStatus(str, Enum):
    trading_session = "trading_session"
    weekend = "weekend"
    holiday = "holiday"


@runtime_checkable
class ExchangeCalendar(Protocol):
    name: str

    def session_status(self, session: date) -> TradingSessionStatus: ...

    def is_trading_session(self, session: date) -> bool: ...

    def trading_sessions(self, start_date: date, end_date: date) -> list[date]: ...

    def previous_trading_session(self, on_or_before: date) -> date | None: ...


@dataclass(frozen=True)
class SimpleKrxCalendar:
    """Minimal KRX-like weekday calendar with caller-supplied holidays."""

    holidays: tuple[date | str, ...] = field(default_factory=tuple)
    name: str = "simple_krx"

    def __post_init__(self) -> None:
        parsed = tuple(sorted({_coerce_date(value) for value in self.holidays}))
        object.__setattr__(self, "holidays", parsed)

    def session_status(self, session: date) -> TradingSessionStatus:
        if session.weekday() >= 5:
            return TradingSessionStatus.weekend
        if session in self.holidays:
            return TradingSessionStatus.holiday
        return TradingSessionStatus.trading_session

    def is_trading_session(self, session: date) -> bool:
        return self.session_status(session) == TradingSessionStatus.trading_session

    def trading_sessions(self, start_date: date, end_date: date) -> list[date]:
        if start_date > end_date:
            return []
        sessions: list[date] = []
        current = start_date
        while current <= end_date:
            if self.is_trading_session(current):
                sessions.append(current)
            current += timedelta(days=1)
        return sessions

    def previous_trading_session(self, on_or_before: date) -> date | None:
        current = on_or_before
        for _ in range(366):
            if self.is_trading_session(current):
                return current
            current -= timedelta(days=1)
        return None


@dataclass(frozen=True)
class HistoricalDataQualityIssue:
    code: str
    message: str
    severity: IssueSeverity = "warning"
    symbol: str | None = None
    session_date: date | str | None = None
    blocking: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "blocking": self.blocking,
            "symbol": self.symbol,
            "session_date": _serialize(self.session_date),
        }
        if self.details:
            payload["details"] = _serialize(self.details)
        return payload


@dataclass(frozen=True)
class HistoricalDataFreshnessPolicy:
    """Requires each symbol's latest bar to cover the latest complete session."""

    block_stale_latest_bar: bool = True


@dataclass(frozen=True)
class MissingBarPolicy:
    """Requires one daily bar per requested symbol for each trading session."""

    block_missing_bars: bool = True


@dataclass(frozen=True)
class HistoricalDataQualityReport:
    provider_name: str
    market: str
    calendar_name: str
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    expected_session_count: int
    expected_latest_session: date | None
    observed_session_count_by_symbol: dict[str, int]
    bar_count: int
    issues: tuple[HistoricalDataQualityIssue, ...] = field(default_factory=tuple)

    @property
    def blocking_issues(self) -> tuple[HistoricalDataQualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def has_blocking_issues(self) -> bool:
        return bool(self.blocking_issues)

    @property
    def status(self) -> Literal["passed", "warning", "blocked"]:
        if self.has_blocking_issues:
            return "blocked"
        if self.issues:
            return "warning"
        return "passed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "has_blocking_issues": self.has_blocking_issues,
            "provider_name": self.provider_name,
            "market": self.market,
            "calendar": self.calendar_name,
            "symbols": list(self.symbols),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "expected_session_count": self.expected_session_count,
            "expected_latest_session": (
                self.expected_latest_session.isoformat() if self.expected_latest_session else None
            ),
            "observed_session_count_by_symbol": dict(self.observed_session_count_by_symbol),
            "bar_count": self.bar_count,
            "issues": [issue.as_dict() for issue in self.issues],
        }


def evaluate_historical_data_quality(
    rows: Sequence[Mapping[str, Any]],
    *,
    symbols: Sequence[str],
    start_date: date,
    end_date: date,
    market: str,
    provider_name: str,
    calendar: ExchangeCalendar | None = None,
    freshness_policy: HistoricalDataFreshnessPolicy | None = None,
    missing_bar_policy: MissingBarPolicy | None = None,
    provider_issues: Iterable[HistoricalDataQualityIssue | Mapping[str, Any]] = (),
) -> HistoricalDataQualityReport:
    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date")

    calendar = calendar or SimpleKrxCalendar()
    freshness_policy = freshness_policy or HistoricalDataFreshnessPolicy()
    missing_bar_policy = missing_bar_policy or MissingBarPolicy()
    request_symbols = tuple(_normalize_symbol(symbol) for symbol in symbols)
    expected_sessions = calendar.trading_sessions(start_date, end_date)
    expected_latest_session = _expected_latest_session(calendar, start_date, end_date)
    issues = [_quality_issue_from_any(issue) for issue in provider_issues]

    seen: set[tuple[str, date]] = set()
    dates_by_symbol: dict[str, list[date]] = {symbol: [] for symbol in request_symbols}

    for index, row in enumerate(rows, start=1):
        symbol = _row_symbol(row)
        session = _row_date(row)
        if symbol is None:
            issues.append(
                _blocking_issue(
                    "symbol_mismatch",
                    f"row {index} is missing symbol/ticker",
                    details={"row_index": index},
                )
            )
            continue
        if symbol not in request_symbols:
            issues.append(
                _blocking_issue(
                    "symbol_mismatch",
                    f"row {index} has unexpected symbol {symbol}",
                    symbol=symbol,
                    session_date=session,
                    details={"expected_symbols": list(request_symbols), "row_index": index},
                )
            )
        if session is None:
            issues.append(
                _blocking_issue(
                    "invalid_session_date",
                    f"row {index} is missing or has an invalid session date",
                    symbol=symbol,
                    details={"row_index": index},
                )
            )
            continue
        if session < start_date or session > end_date:
            continue

        key = (symbol, session)
        if key in seen:
            issues.append(
                _blocking_issue(
                    "duplicate_bar",
                    f"duplicate bar for {symbol} on {session.isoformat()}",
                    symbol=symbol,
                    session_date=session,
                    details={"row_index": index},
                )
            )
        seen.add(key)
        if symbol in dates_by_symbol:
            dates_by_symbol[symbol].append(session)

        issues.extend(_validate_ohlcv(row, symbol=symbol, session=session, row_index=index))

    for symbol, sessions in dates_by_symbol.items():
        if _is_non_monotonic(sessions):
            issues.append(
                _blocking_issue(
                    "non_monotonic_dates",
                    f"bars for {symbol} are not monotonic by session date",
                    symbol=symbol,
                    details={"dates": [session.isoformat() for session in sessions]},
                )
            )

    if missing_bar_policy.block_missing_bars:
        for symbol, sessions in dates_by_symbol.items():
            observed = set(sessions)
            for expected in expected_sessions:
                if expected not in observed:
                    issues.append(
                        _blocking_issue(
                            "missing_bar",
                            f"missing bar for {symbol} on trading session {expected.isoformat()}",
                            symbol=symbol,
                            session_date=expected,
                        )
                    )

    if freshness_policy.block_stale_latest_bar and expected_latest_session is not None:
        for symbol, sessions in dates_by_symbol.items():
            trading_sessions = sorted(session for session in sessions if calendar.is_trading_session(session))
            if trading_sessions and trading_sessions[-1] < expected_latest_session:
                issues.append(
                    _blocking_issue(
                        "stale_latest_bar",
                        (
                            f"latest bar for {symbol} is {trading_sessions[-1].isoformat()}, "
                            f"before expected latest session {expected_latest_session.isoformat()}"
                        ),
                        symbol=symbol,
                        session_date=expected_latest_session,
                        details={"latest_bar": trading_sessions[-1].isoformat()},
                    )
                )

    observed_counts = {
        symbol: len({session for session in sessions if calendar.is_trading_session(session)})
        for symbol, sessions in dates_by_symbol.items()
    }
    return HistoricalDataQualityReport(
        provider_name=provider_name,
        market=market,
        calendar_name=calendar.name,
        symbols=request_symbols,
        start_date=start_date,
        end_date=end_date,
        expected_session_count=len(expected_sessions),
        expected_latest_session=expected_latest_session,
        observed_session_count_by_symbol=observed_counts,
        bar_count=len(rows),
        issues=tuple(issues),
    )


def _validate_ohlcv(
    row: Mapping[str, Any],
    *,
    symbol: str,
    session: date,
    row_index: int,
) -> list[HistoricalDataQualityIssue]:
    issues: list[HistoricalDataQualityIssue] = []
    prices = {field_name: _number(row.get(field_name)) for field_name in ("open", "high", "low", "close")}
    if any(value is None for value in prices.values()):
        issues.append(
            _blocking_issue(
                "invalid_ohlc",
                "OHLC fields must be present and numeric",
                symbol=symbol,
                session_date=session,
                details={"row_index": row_index},
            )
        )
    else:
        open_price = prices["open"]
        high_price = prices["high"]
        low_price = prices["low"]
        close_price = prices["close"]
        assert open_price is not None
        assert high_price is not None
        assert low_price is not None
        assert close_price is not None
        if min(open_price, high_price, low_price, close_price) <= 0:
            issues.append(
                _blocking_issue(
                    "invalid_ohlc",
                    "OHLC prices must be positive",
                    symbol=symbol,
                    session_date=session,
                    details={"row_index": row_index},
                )
            )
        if high_price < low_price:
            issues.append(
                _blocking_issue(
                    "invalid_ohlc",
                    f"high {high_price} is below low {low_price}",
                    symbol=symbol,
                    session_date=session,
                    details={"row_index": row_index},
                )
            )
        if open_price < low_price or open_price > high_price or close_price < low_price or close_price > high_price:
            issues.append(
                _blocking_issue(
                    "invalid_ohlc",
                    "open and close must fall within low/high range",
                    symbol=symbol,
                    session_date=session,
                    details={"row_index": row_index},
                )
            )
    volume = _number(row.get("volume"))
    if volume is None or volume <= 0:
        issues.append(
            _blocking_issue(
                "invalid_volume",
                "volume must be positive",
                symbol=symbol,
                session_date=session,
                details={"row_index": row_index},
            )
        )
    return issues


def _expected_latest_session(calendar: ExchangeCalendar, start_date: date, end_date: date) -> date | None:
    latest = calendar.previous_trading_session(end_date)
    if latest is None or latest < start_date:
        return None
    return latest


def _quality_issue_from_any(issue: HistoricalDataQualityIssue | Mapping[str, Any]) -> HistoricalDataQualityIssue:
    if isinstance(issue, HistoricalDataQualityIssue):
        return issue
    raw = dict(issue)
    severity = _issue_severity(raw.get("severity", "warning"))
    return HistoricalDataQualityIssue(
        code=str(raw.get("code", "provider_quality_issue")),
        message=str(raw.get("message", "provider reported a quality issue")),
        severity=severity,
        symbol=_optional_symbol(raw.get("symbol")),
        session_date=raw.get("session_date"),
        blocking=bool(raw.get("blocking", severity == "error")),
        details=dict(raw.get("details", {})) if isinstance(raw.get("details"), Mapping) else {},
    )


def _blocking_issue(
    code: str,
    message: str,
    *,
    symbol: str | None = None,
    session_date: date | str | None = None,
    details: dict[str, Any] | None = None,
) -> HistoricalDataQualityIssue:
    return HistoricalDataQualityIssue(
        code=code,
        message=message,
        severity="error",
        symbol=symbol,
        session_date=session_date,
        blocking=True,
        details=details or {},
    )


def _normalize_symbol(value: str) -> str:
    cleaned = str(value).strip().upper()
    if not cleaned:
        raise ValueError("symbols must not contain empty values")
    return cleaned


def _optional_symbol(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip().upper()
    return cleaned or None


def _row_symbol(row: Mapping[str, Any]) -> str | None:
    return _optional_symbol(row.get("symbol", row.get("ticker")))


def _row_date(row: Mapping[str, Any]) -> date | None:
    value = row.get("_date", row.get("date", row.get("session_date")))
    try:
        return _coerce_date(value)
    except (TypeError, ValueError):
        return None


def _coerce_date(value: Any) -> date:
    if isinstance(value, datetime):
        raise ValueError("session date must not be a datetime")
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if len(raw) == 8 and raw.isdigit():
        raw = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    if "T" in raw or " " in raw or "+" in raw or raw.endswith("Z"):
        raise ValueError("session date must be a plain date")
    return date.fromisoformat(raw)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _is_non_monotonic(sessions: list[date]) -> bool:
    if len(sessions) < 3:
        return False
    return sessions != sorted(sessions) and sessions != sorted(sessions, reverse=True)


def _serialize(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    return value
