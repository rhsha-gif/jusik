"""Causal, offline-only minute replay for one frozen intraday specification.

Minute OHLC bars are an execution approximation, not tick-level fill evidence.  A
bar becomes observable only at ``available_at`` and its close/volume may be used
only then.  Orders are eligible after the configured latency and fill once at a
subsequent observable event, bounded by reported event volume.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any, Mapping

from quantpilot.paper.calendar import Calendar, KST
from quantpilot.paper.intraday.strategy import (
    SPECS,
    Spec,
    costs,
    evaluate,
    protective_exit,
)
from quantpilot.paper.strategy import Bar, Signal
from quantpilot.paper.intraday.deployment import VALIDATED_POLICY


CAPITAL = VALIDATED_POLICY["initial_capital"]
MAX_NAMES = VALIDATED_POLICY["max_positions"]
MAX_NAME_FRACTION = VALIDATED_POLICY["symbol_cap"]
MAX_TRADE_LOSS_FRACTION = VALIDATED_POLICY["trade_risk"]
MAX_DAILY_LOSS_FRACTION = VALIDATED_POLICY["daily_loss_limit"]
MAX_PEAK_DRAWDOWN_FRACTION = VALIDATED_POLICY["peak_drawdown_limit"]
FEE_BPS = VALIDATED_POLICY["fee_bps"]
SELL_TAX_BPS = VALIDATED_POLICY["sell_tax_bps"]
ENTRY_CUTOFF_MINUTES = VALIDATED_POLICY["entry_cutoff_minutes"]
LIQUIDATION_MINUTES = VALIDATED_POLICY["liquidation_minutes"]
_UTC = timezone.utc
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DATA_MODES = {
    "fixture",
    "local_historical",
    "external_historical",
    "realtime_market_data",
    "paper_trading",
    "live_trading_candidate",
    "live_canary",
    "live_scaled",
}


@dataclass(frozen=True)
class _ObservedBar:
    bar: Bar
    available_at: datetime
    source: str


@dataclass(frozen=True)
class _MarketEvent:
    at: datetime
    symbol: str
    price: float
    sell_price: float
    volume: float
    source: str
    bar: _ObservedBar | None = None


@dataclass
class _Order:
    signal: Signal
    decision_at: datetime
    eligible_at: datetime
    session_day: str


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field}_not_iso")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field}_not_iso") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}_not_aware")
    return parsed.astimezone(_UTC)


def _number(value: object, field: str, *, positive: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}_invalid")
    result = float(value)
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise ValueError(f"{field}_invalid")
    return result


def _canonical_hash(dataset: Mapping[str, Any]) -> str:
    payload = dict(dataset)
    payload.pop("sha256", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _issue(issues: list[str], value: str) -> None:
    if value not in issues:
        issues.append(value)


def _session(calendar: object, at: datetime):
    try:
        session = calendar.session(at)
    except (TypeError, ValueError):
        session = calendar.session(at.astimezone(KST).date().isoformat())
    if session is None:
        return None
    session_open = getattr(session, "open", getattr(session, "opens", None))
    session_close = getattr(session, "close", getattr(session, "closes", None))
    if not isinstance(session_open, datetime) or not isinstance(
        session_close, datetime
    ):
        raise ValueError("calendar_session_invalid")
    if session_open.tzinfo is None or session_close.tzinfo is None:
        raise ValueError("calendar_session_not_aware")
    return session_open.astimezone(_UTC), session_close.astimezone(_UTC)


def _parse_day(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("day_not_iso")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError("day_not_iso") from None


def _day_session(calendar: object, day: str):
    probe = datetime.combine(date.fromisoformat(day), time(12), KST)
    return _session(calendar, probe)


def _row_bar(row: object) -> _ObservedBar:
    if not isinstance(row, Mapping):
        raise ValueError("bar_not_object")
    start = _timestamp(row.get("start"), "bar_start")
    available = _timestamp(row.get("available_at"), "bar_available_at")
    source = row.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("bar_source_invalid")
    bar = Bar(
        symbol=str(row.get("symbol", "")),
        start=start,
        open=_number(row.get("open"), "bar_open"),
        high=_number(row.get("high"), "bar_high"),
        low=_number(row.get("low"), "bar_low"),
        close=_number(row.get("close"), "bar_close"),
        volume=_number(row.get("volume"), "bar_volume", positive=False),
    )
    return _ObservedBar(bar, available, source)


def _event_prices(row: Mapping[str, Any]) -> tuple[float, float]:
    kind = str(row.get("type", row.get("kind", ""))).lower()
    if kind == "quote" or "ask" in row or "bid" in row:
        bid = _number(row.get("bid"), "event_bid")
        ask = _number(row.get("ask"), "event_ask")
        if ask < bid:
            raise ValueError("event_crossed_quote")
        return ask, bid
    for key in ("price", "last", "close"):
        if key in row:
            price = _number(row.get(key), "event_price")
            return price, price
    raise ValueError("event_price_invalid")


def _row_event(row: object) -> _MarketEvent:
    if not isinstance(row, Mapping):
        raise ValueError("event_not_object")
    at = _timestamp(
        row.get("available_at", row.get("at", row.get("timestamp"))), "event_at"
    )
    source = row.get("source")
    symbol = row.get("symbol")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("event_source_invalid")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("event_symbol_invalid")
    raw_volume = row.get(
        "volume", row.get("ask_size", row.get("size", row.get("quantity", 0)))
    )
    buy_price, sell_price = _event_prices(row)
    return _MarketEvent(
        at=at,
        symbol=symbol,
        price=buy_price,
        sell_price=sell_price,
        volume=_number(raw_volume, "event_volume", positive=False),
        source=source,
    )


def _parse_bars(dataset: Mapping[str, Any], issues: list[str]) -> list[_ObservedBar]:
    rows = dataset.get("bars", [])
    if not isinstance(rows, list):
        _issue(issues, "pit_invalid:bars_not_list")
        return []
    parsed: list[_ObservedBar] = []
    for index, row in enumerate(rows):
        try:
            observed = _row_bar(row)
        except (TypeError, ValueError) as exc:
            _issue(issues, f"pit_invalid:bar[{index}]:{exc}")
            continue
        complete_at = observed.bar.start + timedelta(minutes=1)
        if observed.available_at < complete_at:
            _issue(
                issues,
                f"pit_invalid:future_bar:{observed.bar.symbol}:{observed.bar.start.isoformat()}",
            )
            continue
        # Delayed data remains available only at its actual arrival, never backdated.
        parsed.append(observed)

    grouped: dict[tuple[str, datetime], list[_ObservedBar]] = defaultdict(list)
    for observed in parsed:
        grouped[(observed.bar.symbol, observed.bar.start)].append(observed)
    clean: list[_ObservedBar] = []
    for (symbol, start), group in sorted(grouped.items()):
        if len(group) != 1:
            _issue(
                issues,
                f"pit_invalid:duplicate_or_revision:{symbol}:{start.isoformat()}",
            )
            continue
        clean.append(group[0])
    return sorted(clean, key=lambda item: (item.available_at, item.bar.symbol))


def _parse_events(dataset: Mapping[str, Any], issues: list[str]) -> list[_MarketEvent]:
    rows = dataset.get("events", [])
    if not isinstance(rows, list):
        _issue(issues, "pit_invalid:events_not_list")
        return []
    events: list[_MarketEvent] = []
    seen: set[tuple[str, datetime, str]] = set()
    for index, row in enumerate(rows):
        try:
            event = _row_event(row)
        except (TypeError, ValueError) as exc:
            _issue(issues, f"pit_invalid:event[{index}]:{exc}")
            continue
        key = (event.symbol, event.at, event.source)
        if key in seen:
            _issue(
                issues,
                f"pit_invalid:duplicate_event:{event.symbol}:{event.at.isoformat()}",
            )
            continue
        seen.add(key)
        events.append(event)
    return sorted(events, key=lambda item: (item.at, item.symbol, item.source))


def _parse_universes(
    dataset: Mapping[str, Any], issues: list[str]
) -> list[dict[str, Any]]:
    rows = dataset.get("universes", [])
    if not isinstance(rows, list):
        _issue(issues, "pit_invalid:universes_not_list")
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[datetime, str]] = set()
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, Mapping):
                raise ValueError("not_object")
            at = _timestamp(row.get("at"), "universe_at")
            source = row.get("source")
            symbols = row.get("symbols")
            metadata = row.get("metadata")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("source_invalid")
            if not isinstance(symbols, list) or any(
                not isinstance(symbol, str) or not symbol.strip() for symbol in symbols
            ):
                raise ValueError("symbols_invalid")
            if not isinstance(metadata, Mapping) or not metadata:
                raise ValueError("ranking_metadata_missing")
            if (at, source) in seen:
                raise ValueError("duplicate_or_revision")
            seen.add((at, source))
            result.append(
                {
                    "at": at,
                    "source": source,
                    "symbols": tuple(dict.fromkeys(symbols)),
                    "metadata": dict(metadata),
                }
            )
        except (TypeError, ValueError) as exc:
            _issue(issues, f"pit_invalid:universe[{index}]:{exc}")
    return sorted(result, key=lambda item: (item["at"], item["source"]))


def _pit_universe(
    universes: list[dict[str, Any]],
    at: datetime,
    session_open: datetime,
    session_close: datetime,
) -> tuple[str, ...] | None:
    usable = [
        item
        for item in universes
        if session_open <= item["at"] <= at and item["at"] < session_close
    ]
    return (
        usable[-1]["symbols"]
        if usable and at - usable[-1]["at"] <= timedelta(minutes=5)
        else None
    )


def _result_base(
    *,
    spec: Spec,
    dataset: Mapping[str, Any],
    data_mode: object,
    trading_days: list[str],
    issues: list[str],
    slippage_bps: float,
    latency_minutes: int,
    participation: float,
    fill_fraction: float,
) -> dict[str, Any]:
    return {
        "version": spec.version,
        "strategy_id": spec.strategy_id,
        "spec": {
            "strategy_id": spec.strategy_id,
            "lookback": spec.lookback,
            "stop_atr": spec.stop_atr,
            "reward_r": spec.reward_r,
            "max_hold_minutes": spec.max_hold_minutes,
            "volume_ratio": spec.volume_ratio,
        },
        "dataset_hash": (
            dataset.get("sha256") if isinstance(dataset.get("sha256"), str) else None
        ),
        "data_mode": data_mode,
        "trades": [],
        "daily_pnl": {day: 0.0 for day in trading_days},
        "round_trips": 0,
        "net_pnl": 0.0,
        "unresolved": [],
        "quality_issues": issues,
        "trading_days": trading_days,
        "assumptions": {
            "capital": CAPITAL,
            "max_names": MAX_NAMES,
            "max_name_fraction": MAX_NAME_FRACTION,
            "max_trade_loss_fraction": MAX_TRADE_LOSS_FRACTION,
            "max_daily_loss_fraction": MAX_DAILY_LOSS_FRACTION,
            "max_peak_drawdown_fraction": MAX_PEAK_DRAWDOWN_FRACTION,
            "fee_bps_per_side": FEE_BPS,
            "sell_tax_bps": SELL_TAX_BPS,
            "slippage_bps": slippage_bps,
            "latency_minutes": latency_minutes,
            "participation": participation,
            "fill_fraction": fill_fraction,
            "execution_model": "completed-minute OHLC approximation; not tick validation",
            "entry_fill": "one bounded partial fill at the next eligible observable event; remainder cancelled",
            "bar_policy": "no gap filling; warmup resets after each missing minute",
            "exit_priority": "stop before target; stop gaps use the adverse observable open",
            "final_liquidation": "none when the session closing bar is missing",
        },
    }


def replay(
    dataset,
    spec,
    *,
    days=None,
    slippage_bps=5,
    calibration=None,
    latency_minutes=1,
    participation=0.01,
    fill_fraction=1,
    adverse_gap_bps=0,
    calendar=None,
):
    """Replay one frozen spec against point-in-time offline observations."""

    if not isinstance(dataset, Mapping):
        raise TypeError("dataset must be a mapping")
    if not isinstance(spec, Spec) or spec not in SPECS:
        raise ValueError("spec must be one of SPECS")
    if days is not None and (
        not isinstance(days, (list, tuple, set)) or isinstance(days, str)
    ):
        raise TypeError("days must be a collection of ISO dates or None")
    selected_days = None if days is None else sorted({_parse_day(day) for day in days})
    slippage = _number(slippage_bps, "slippage_bps", positive=False)
    adverse_gap = _number(adverse_gap_bps, "adverse_gap_bps", positive=False)
    if adverse_gap > 1000:
        raise ValueError("adverse_gap_bps must be <= 1000")
    if (
        isinstance(latency_minutes, bool)
        or not isinstance(latency_minutes, int)
        or latency_minutes < 1
    ):
        raise ValueError("latency_minutes must be an integer >= 1")
    participation_value = _number(participation, "participation")
    fill_value = _number(fill_fraction, "fill_fraction", positive=False)
    if participation_value > 1 or fill_value > 1:
        raise ValueError("participation and fill_fraction must be <= 1")

    issues: list[str] = []
    if dataset.get("schema_version") != 1:
        _issue(issues, "pit_invalid:schema_version")
    data_mode = dataset.get("data_mode")
    if data_mode not in _DATA_MODES:
        _issue(issues, "pit_invalid:data_mode")
    if not isinstance(dataset.get("provenance"), Mapping) or not dataset.get(
        "provenance"
    ):
        _issue(issues, "pit_invalid:provenance_missing")
    supplied_hash = dataset.get("sha256")
    if not isinstance(supplied_hash, str) or not _SHA256.fullmatch(supplied_hash):
        _issue(issues, "pit_invalid:sha256_missing_or_invalid")
    else:
        try:
            if supplied_hash != _canonical_hash(dataset):
                _issue(issues, "pit_invalid:sha256_mismatch")
        except (TypeError, ValueError):
            _issue(issues, "pit_invalid:dataset_not_json_safe")

    observed_bars = _parse_bars(dataset, issues)
    # Tick execution has its own trade-through simulator. Never mix top-of-book
    # touch fills into minute results or double-count bar and tape liquidity.
    explicit_events = []
    universes = _parse_universes(dataset, issues)
    if data_mode != "fixture":
        from quantpilot.paper.intraday.universe import eligible_symbols

        for snapshot in universes:
            allowed, problems = eligible_symbols(snapshot, snapshot["at"])
            snapshot["symbols"] = tuple(allowed)
            for problem in problems:
                _issue(issues, problem)
    replay_calendar = calendar or Calendar()

    observed_days: set[str] = set()
    bar_sessions: dict[tuple[str, datetime], tuple[datetime, datetime, str]] = {}
    eligible_bars: list[_ObservedBar] = []
    for observed in observed_bars:
        session = _session(replay_calendar, observed.bar.start)
        if session is None or not (session[0] <= observed.bar.start < session[1]):
            _issue(
                issues,
                f"missing_coverage:bar_outside_session:{observed.bar.symbol}:{observed.bar.start.isoformat()}",
            )
            continue
        day = observed.bar.start.astimezone(KST).date().isoformat()
        observed_days.add(day)
        if selected_days is not None and day not in selected_days:
            continue
        bar_sessions[(observed.bar.symbol, observed.bar.start)] = (
            session[0],
            session[1],
            day,
        )
        eligible_bars.append(observed)

    candidate_days = sorted(observed_days if selected_days is None else selected_days)
    trading_days: list[str] = []
    sessions_by_day: dict[str, tuple[datetime, datetime]] = {}
    for day in candidate_days:
        session = _day_session(replay_calendar, day)
        if session is not None:
            trading_days.append(day)
            sessions_by_day[day] = session

    result = _result_base(
        spec=spec,
        dataset=dataset,
        data_mode=data_mode,
        trading_days=trading_days,
        issues=issues,
        slippage_bps=slippage,
        latency_minutes=latency_minutes,
        participation=participation_value,
        fill_fraction=fill_value,
    )
    result["assumptions"]["additional_stop_gap_bps"] = adverse_gap

    if not eligible_bars:
        for day in trading_days:
            _issue(issues, f"missing_coverage:no_bars:{day}")
        return result

    bars_by_symbol: dict[str, list[Bar]] = defaultdict(list)
    last_start: dict[tuple[str, str], datetime] = {}
    pending_orders: dict[str, _Order] = {}
    positions: dict[str, dict[str, Any]] = {}
    pending_updates: dict[str, tuple[float, str | None, datetime]] = {}
    realized_by_day = result["daily_pnl"]
    equity = CAPITAL
    peak_equity = CAPITAL
    drawdown_halted = False
    halted_days: set[str] = set()
    day_bases = {}
    marks = {}
    fill_capacity = {}

    def marked_equity():
        return equity + sum(
            (
                marks.get(s, p["entry_price"])
                - p["entry_price"]
                - costs(
                    p["entry_price"],
                    marks.get(s, p["entry_price"]),
                    slippage_bps=slippage,
                )
            )
            * p["quantity"]
            for s, p in positions.items()
        )

    market_events = list(explicit_events)
    for observed in eligible_bars:
        market_events.append(
            _MarketEvent(
                at=observed.available_at,
                symbol=observed.bar.symbol,
                price=observed.bar.close,
                sell_price=observed.bar.close,
                volume=observed.bar.volume,
                source=observed.source,
                bar=observed,
            )
        )
    market_events.sort(
        key=lambda item: (item.at, item.symbol, 0 if item.bar else 1, item.source)
    )

    def reserved_loss() -> float:
        return sum(position["planned_loss"] for position in positions.values())

    def close_position(
        symbol: str, at: datetime, price: float, reason: str, capacity=None
    ) -> None:
        nonlocal equity, peak_equity, drawdown_halted
        position = positions[symbol]
        pending_updates.pop(symbol, None)
        position["exit_reason"] = reason
        if reason == "stop":
            price *= 1 - adverse_gap / 10000
        remaining_capacity = fill_capacity.get((symbol, at), capacity)
        quantity = (
            min(position["quantity"], remaining_capacity)
            if remaining_capacity is not None
            else position["quantity"]
        )
        if quantity <= 0:
            return
        if remaining_capacity is not None:
            fill_capacity[(symbol, at)] = remaining_capacity - quantity
        net = (
            (price - position["entry_price"])
            - costs(
                position["entry_price"],
                price,
                fee_bps=FEE_BPS,
                tax_bps=SELL_TAX_BPS,
                slippage_bps=slippage,
            )
        ) * quantity
        risk = position["risk_per_share"] * position["initial_quantity"]
        day = at.astimezone(KST).date().isoformat()
        if day in realized_by_day:
            realized_by_day[day] += net
        equity += net
        position["net_pnl"] += net
        position["exit_notional"] += price * quantity
        position["quantity"] -= quantity
        position["planned_loss"] = position["quantity"] * position["risk_per_share"]
        position["exit_reason"] = reason
        peak_equity = max(peak_equity, equity)
        if not position["quantity"]:
            positions.pop(symbol)
            result["trades"].append(
                {
                    "symbol": symbol,
                    "entry_at": position["entry_at"].isoformat().replace("+00:00", "Z"),
                    "exit_at": at.isoformat().replace("+00:00", "Z"),
                    "quantity": position["initial_quantity"],
                    "entry_price": float(position["entry_price"]),
                    "exit_price": float(
                        position["exit_notional"] / position["initial_quantity"]
                    ),
                    "net_pnl": float(position["net_pnl"]),
                    "r_multiple": (
                        float(position["net_pnl"] / risk) if risk > 0 else 0.0
                    ),
                    "reason": reason,
                }
            )
        if equity <= peak_equity * (1.0 - MAX_PEAK_DRAWDOWN_FRACTION):
            drawdown_halted = True
        if (
            realized_by_day.get(day, 0.0)
            <= -day_bases.get(day, CAPITAL) * MAX_DAILY_LOSS_FRACTION
        ):
            halted_days.add(day)

    index = 0
    while index < len(market_events):
        at = market_events[index].at
        batch: list[_MarketEvent] = []
        while index < len(market_events) and market_events[index].at == at:
            batch.append(market_events[index])
            index += 1

        for event in batch:
            symbol = event.symbol
            session = _session(replay_calendar, at)
            if session is None:
                continue
            event_day = at.astimezone(KST).date().isoformat()
            if event_day not in realized_by_day:
                continue
            day_bases.setdefault(event_day, marked_equity())
            marks[symbol] = event.sell_price
            marked = marked_equity()
            peak_equity = max(peak_equity, marked)
            if marked <= peak_equity * (1 - MAX_PEAK_DRAWDOWN_FRACTION):
                drawdown_halted = True
            if marked <= day_bases[event_day] * (1 - MAX_DAILY_LOSS_FRACTION):
                halted_days.add(event_day)
            capacity = math.floor(event.volume * participation_value * fill_value)
            fill_capacity[(symbol, at)] = capacity

            intrabar_stop = positions.get(symbol, {}).get("stop")
            deferred_update = pending_updates.pop(symbol, None)
            if deferred_update is not None and symbol in positions:
                next_stop, pending_reason, decided_at = deferred_update
                old_stop = positions[symbol]["stop"]
                positions[symbol]["stop"] = max(positions[symbol]["stop"], next_stop)
                if (
                    event.bar
                    and event.bar.bar.start < decided_at
                    and next_stop > old_stop
                ):
                    if old_stop < event.bar.bar.low <= next_stop:
                        _issue(
                            issues,
                            f"execution_ambiguous:stop_update_overlaps_bar:{symbol}:{at.isoformat()}",
                        )
                else:
                    intrabar_stop = positions[symbol]["stop"]
                if pending_reason:
                    close_position(
                        symbol, at, event.sell_price, pending_reason, capacity
                    )

            entered_now = False
            order = pending_orders.get(symbol)
            if (
                order is not None
                and at >= order.eligible_at
                and symbol not in positions
            ):
                if order.session_day != event_day or at >= session[1] - timedelta(
                    minutes=ENTRY_CUTOFF_MINUTES
                ):
                    pending_orders.pop(symbol, None)
                elif (
                    not (order.signal.stop < event.price < order.signal.target)
                    or abs(event.price / order.signal.price - 1) > 0.005
                ):
                    _issue(
                        issues,
                        f"execution_invalid:entry_price_gap:{symbol}:{at.isoformat()}",
                    )
                    pending_orders.pop(symbol, None)
                else:
                    per_share_risk = (event.price - order.signal.stop) + costs(
                        event.price,
                        order.signal.stop,
                        fee_bps=FEE_BPS,
                        tax_bps=SELL_TAX_BPS,
                        slippage_bps=slippage,
                    )
                    if (
                        order.signal.target
                        - event.price
                        - costs(event.price, order.signal.target, slippage_bps=slippage)
                        <= 0
                    ):
                        pending_orders.pop(symbol, None)
                        continue
                    sizing_equity = min(equity, marked_equity())
                    trade_budget = sizing_equity * MAX_TRADE_LOSS_FRACTION
                    daily_room = max(
                        0.0,
                        day_bases[event_day] * MAX_DAILY_LOSS_FRACTION
                        - max(0, day_bases[event_day] - marked_equity())
                        - reserved_loss(),
                    )
                    drawdown_floor = peak_equity * (1.0 - MAX_PEAK_DRAWDOWN_FRACTION)
                    drawdown_room = max(0.0, equity - drawdown_floor - reserved_loss())
                    desired = math.floor(
                        min(
                            sizing_equity
                            * MAX_NAME_FRACTION
                            / (event.price * (1 + FEE_BPS / 10000)),
                            trade_budget / per_share_risk,
                            daily_room / per_share_risk,
                            drawdown_room / per_share_risk,
                        )
                    )
                    capacity = math.floor(
                        event.volume * participation_value * fill_value
                    )
                    quantity = min(desired, capacity)
                    pending_orders.pop(symbol, None)
                    if (
                        quantity > 0
                        and len(positions) < MAX_NAMES
                        and not drawdown_halted
                        and order.session_day not in halted_days
                    ):
                        positions[symbol] = {
                            "symbol": symbol,
                            "entry_at": at,
                            "entry_price": event.price,
                            "quantity": quantity,
                            "stop": order.signal.stop,
                            "target": order.signal.target,
                            "risk_per_share": per_share_risk,
                            "planned_loss": quantity * per_share_risk,
                            "session_day": order.session_day,
                            "opened": at,
                            "initial_quantity": quantity,
                            "net_pnl": 0.0,
                            "exit_notional": 0.0,
                        }
                        entered_now = True

            observed = event.bar
            if observed is None:
                if symbol in positions and not entered_now:
                    if event.sell_price <= positions[symbol]["stop"]:
                        close_position(symbol, at, event.sell_price, "stop")
                    elif event.sell_price >= positions[symbol]["target"]:
                        close_position(
                            symbol,
                            at,
                            positions[symbol]["target"],
                            (
                                "vwap_target"
                                if spec.strategy_id == "range_reversion"
                                else "target"
                            ),
                        )
                continue
            bar = observed.bar
            session_info = bar_sessions.get((bar.symbol, bar.start))
            if session_info is None:
                continue
            session_open, session_close, bar_day = session_info
            segment_key = (bar.symbol, bar_day)
            previous_start = last_start.get(segment_key)
            if previous_start is not None and bar.start - previous_start != timedelta(
                minutes=1
            ):
                bars_by_symbol[bar.symbol] = []
                _issue(
                    issues,
                    f"missing_coverage:bar_gap:{bar.symbol}:{bar.start.isoformat()}",
                )
            last_start[segment_key] = bar.start

            position = positions.get(symbol)
            if position is not None and not entered_now:
                full_interval = bar.start >= position["entry_at"]
                if full_interval and bar.low <= intrabar_stop:
                    exit_price = min(bar.open, intrabar_stop)
                    close_position(symbol, at, exit_price, "stop", capacity)
                elif bar.close <= position["stop"]:
                    close_position(symbol, at, bar.close, "stop", capacity)
                elif (bar.high if full_interval else bar.close) >= position["target"]:
                    close_position(
                        symbol,
                        at,
                        position["target"],
                        (
                            "vwap_target"
                            if spec.strategy_id == "range_reversion"
                            else "target"
                        ),
                        capacity,
                    )
                elif position.get("exit_reason"):
                    close_position(
                        symbol, at, bar.close, position["exit_reason"], capacity
                    )

            bars_by_symbol[symbol].append(bar)
            position = positions.get(symbol)
            if position is not None:
                if position is not None and at >= session_close - timedelta(
                    minutes=LIQUIDATION_MINUTES
                ):
                    close_position(symbol, at, bar.close, "session_close", capacity)
                    position = positions.get(symbol)
                if position is not None:
                    try:
                        next_stop, reason = protective_exit(
                            position, bars_by_symbol[symbol], at, spec
                        )
                    except ValueError as exc:
                        _issue(
                            issues, f"missing_coverage:protective_exit:{symbol}:{exc}"
                        )
                        next_stop, reason = position["stop"], None
                    pending_updates[symbol] = (
                        max(position["stop"], next_stop),
                        reason,
                        at,
                    )

        # Evaluate all symbols together after every bar in this timestamp is visible.
        signals: list[Signal] = []
        signal_sessions: dict[str, tuple[datetime, datetime, str]] = {}
        for event in batch:
            if event.bar is None:
                continue
            observed = event.bar
            bar = observed.bar
            session_info = bar_sessions.get((bar.symbol, bar.start))
            if session_info is None:
                continue
            session_open, session_close, day = session_info
            if (
                bar.symbol in positions
                or bar.symbol in pending_orders
                or drawdown_halted
                or day in halted_days
            ):
                continue
            universe = _pit_universe(universes, at, session_open, session_close)
            if universe is None:
                _issue(
                    issues,
                    f"pit_invalid:no_current_session_universe:{day}:{at.isoformat()}",
                )
                continue
            if bar.symbol not in universe:
                continue
            try:
                found = evaluate(
                    bars_by_symbol[bar.symbol],
                    at,
                    session_open,
                    spec,
                    calibration=calibration,
                )
            except ValueError as exc:
                _issue(issues, f"missing_coverage:evaluate:{bar.symbol}:{exc}")
                continue
            for signal in found:
                signals.append(signal)
                signal_sessions[signal.symbol] = session_open, session_close, day

        for signal in sorted(
            signals, key=lambda item: (-item.score, item.symbol, item.reason)
        ):
            if len(positions) + len(pending_orders) >= MAX_NAMES:
                break
            session_open, session_close, day = signal_sessions[signal.symbol]
            if at + timedelta(minutes=latency_minutes) >= session_close - timedelta(
                minutes=ENTRY_CUTOFF_MINUTES
            ):
                continue
            day_loss = min(0.0, realized_by_day[day])
            if (
                -day_loss + reserved_loss()
                >= day_bases.get(day, CAPITAL) * MAX_DAILY_LOSS_FRACTION
            ):
                break
            pending_orders[signal.symbol] = _Order(
                signal=signal,
                decision_at=at,
                eligible_at=at + timedelta(minutes=latency_minutes),
                session_day=day,
            )

    coverage: dict[tuple[str, str], set[datetime]] = defaultdict(set)
    for observed in eligible_bars:
        info = bar_sessions.get((observed.bar.symbol, observed.bar.start))
        if info is not None:
            coverage[(observed.bar.symbol, info[2])].add(observed.bar.start)
    covered_days = {day for _, day in coverage}
    for day in trading_days:
        if day not in covered_days:
            _issue(issues, "missing_coverage:no_bars:" + day)
    for (symbol, day), starts in sorted(coverage.items()):
        expected_close_bar = sessions_by_day[day][1] - timedelta(minutes=1)
        if expected_close_bar not in starts:
            _issue(issues, f"missing_coverage:session_close_bar:{day}:{symbol}")

    for symbol, position in sorted(positions.items()):
        result["unresolved"].append(
            {
                "symbol": symbol,
                "entry_at": position["entry_at"].isoformat().replace("+00:00", "Z"),
                "quantity": position["quantity"],
                "entry_price": float(position["entry_price"]),
                "reason": "missing_session_close_bar",
            }
        )
        _issue(
            issues, f"missing_coverage:session_close:{position['session_day']}:{symbol}"
        )

    result["round_trips"] = len(result["trades"])
    result["net_pnl"] = float(sum(trade["net_pnl"] for trade in result["trades"]))
    result["daily_pnl"] = {day: float(realized_by_day[day]) for day in trading_days}
    return result


def calibrate(result):
    """Summarize closed training episodes without consulting any other data."""

    if not isinstance(result, Mapping) or not isinstance(
        result.get("trades", []), list
    ):
        raise TypeError("result must contain a trades list")
    values: list[float] = []
    for trade in result.get("trades", []):
        if not isinstance(trade, Mapping):
            raise ValueError("training trade must be a mapping")
        value = trade.get("r_multiple")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("training r_multiple must be finite")
        values.append(float(value))
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    return {
        "round_trips": len(values),
        "p_win": len(wins) / len(values) if values else 0.0,
        "mean_win_r": sum(wins) / len(wins) if wins else 0.0,
        "mean_loss_r": -sum(losses) / len(losses) if losses else 0.0,
    }
