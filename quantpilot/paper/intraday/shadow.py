"""Order-free contemporaneous observations and paired tick/quote simulations."""

from dataclasses import asdict
from datetime import datetime, timedelta
import math

from quantpilot.paper.calendar import KST
from quantpilot.paper.intraday.deployment import digest, VALIDATED_POLICY
from quantpilot.paper.intraday.evaluation import stamp
from quantpilot.paper.intraday.runtime import allocate
from quantpilot.paper.intraday.strategy import (
    costs,
    evaluate,
    get_spec,
    protective_exit,
    rank,
)
from quantpilot.paper.strategy import Bar, Signal

ENTRY_COST_RATE = (
    VALIDATED_POLICY["fee_bps"] + VALIDATED_POLICY["slippage_bps"]
) / 10000
EXIT_COST_RATE = (
    VALIDATED_POLICY["fee_bps"]
    + VALIDATED_POLICY["sell_tax_bps"]
    + VALIDATED_POLICY["slippage_bps"]
) / 10000


def histories_at(dataset, now, session):
    histories = {}
    for raw in dataset["bars"]:
        start = stamp(raw["start"])
        if (
            session.opens <= start
            and start + timedelta(minutes=1) <= now
            and stamp(raw["available_at"]) <= now
        ):
            bar = Bar(
                **{
                    k: start if k == "start" else raw[k]
                    for k in Bar.__dataclass_fields__
                }
            )
            histories.setdefault(bar.symbol, {})[start] = bar
    result = {}
    for symbol, by_time in histories.items():
        ordered = [by_time[t] for t in sorted(by_time)]
        for i in range(len(ordered) - 1, 0, -1):
            if ordered[i].start - ordered[i - 1].start != timedelta(minutes=1):
                ordered = ordered[i:]
                break
        result[symbol] = ordered
    return result


def signals_at(dataset, candidates, now, session):
    snapshots = [
        r for r in dataset["universes"] if session.opens <= stamp(r["at"]) <= now
    ]
    if not snapshots:
        return [], ["point_in_time_universe_missing"]
    latest = max(snapshots, key=lambda r: stamp(r["at"]))
    if now - stamp(latest["at"]) > timedelta(minutes=5):
        return [], ["point_in_time_universe_stale"]
    histories = histories_at(dataset, now, session)
    signals = []
    from quantpilot.paper.intraday.universe import eligible_symbols

    symbols, issues = eligible_symbols(
        latest, now, fixture=dataset["data_mode"] == "fixture"
    )
    for symbol in symbols:
        bars = histories.get(symbol, [])
        if not bars or now - bars[-1].start > timedelta(minutes=2):
            if now >= session.opens + timedelta(minutes=2):
                issues.append("stale_minute:" + symbol)
            continue
        for candidate in candidates:
            signals.extend(
                evaluate(
                    bars,
                    now,
                    session.opens,
                    get_spec(candidate["version"]),
                    candidate["calibration"],
                )
            )
    return signals, issues


def observe(experiment, dataset, now, session, assessment=None):
    candidates = experiment.records("frozen")
    if not candidates:
        return None
    if now >= session.opens + timedelta(minutes=1):
        histories = histories_at(dataset, now, session)
        latest = now.replace(second=0, microsecond=0)
        if not any(
            bars and bars[-1].start + timedelta(minutes=1) == latest
            for bars in histories.values()
        ):
            return None
    signals, issues = signals_at(dataset, candidates, now, session)
    if assessment and (
        not assessment.usable(now)
        or now - assessment.observed_at >= timedelta(minutes=30)
    ):
        assessment = None
    body = {
        "at": now.isoformat(),
        "day": now.astimezone(KST).date().isoformat(),
        "candidate_hashes": {c["strategy_id"]: digest(c) for c in candidates},
        "signals": [asdict(s) for s in signals],
        "issues": issues,
        "rules_weights": allocate(signals, None),
        "ai_weights": allocate(signals, assessment),
        "assessment": assessment.model_dump(mode="json") if assessment else None,
        "data_mode": dataset["data_mode"],
        "dataset_hash_at_observation": dataset["sha256"],
    }
    key = now.replace(second=0, microsecond=0).isoformat()
    if experiment.read("shadow_observation", key) is None:
        experiment.append("shadow_observation", key, body, now)
    return body


def simulate(
    observations,
    events,
    session,
    mode,
    initial=VALIDATED_POLICY["initial_capital"],
    peak=None,
    *,
    halted=False,
    dataset=None,
):
    """Strict trade-through, 1% tape participation, top-book size and one-second latency.

    Queue priority is unknown. These are conservative shadow estimates, never
    actual fills, and no broker/order API is reachable from this simulator.
    """
    cash, peak = float(initial), float(peak or initial)
    positions, pending, books, ticks, seen = {}, {}, {}, {}, set()
    trades, partials, missed = [], 0, 0
    day_halt, peak_halt = False, halted or cash <= peak * (
        1 - VALIDATED_POLICY["peak_drawdown_limit"]
    )
    observations = sorted(observations, key=lambda r: stamp(r["at"]))
    index = 0
    comparisons = 0

    def equity():
        return cash + sum(
            p["quantity"]
            * books.get(s, {"bid": p["entry_price"]})["bid"]
            * (1 - EXIT_COST_RATE)
            for s, p in positions.items()
        )

    def fresh(event, now):
        return (
            event
            and 0
            <= (now - stamp(event["received_at"])).total_seconds()
            < VALIDATED_POLICY["quote_ttl_seconds"]
            and 0
            <= (now - stamp(event["event_at"])).total_seconds()
            < VALIDATED_POLICY["quote_ttl_seconds"]
        )

    def risk_reserved():
        value = sum(
            p["quantity"]
            * max(0, p["entry_price"] - p["stop"] + costs(p["entry_price"], p["stop"]))
            for p in positions.values()
        )
        value += sum(
            p["remaining"] * p["risk"] for p in pending.values() if p["side"] == "buy"
        )
        return value

    for event in sorted(events, key=lambda e: (stamp(e["received_at"]), e["id"])):
        now, symbol = stamp(event["received_at"]), event["symbol"]
        if not session.opens <= now < session.closes or event["id"] in seen:
            continue
        seen.add(event["id"])
        # Decisions precede this event, so this event cannot be used as their quote.
        while index < len(observations) and stamp(observations[index]["at"]) < now:
            observation = observations[index]
            index += 1
            decision = stamp(observation["at"])
            if dataset and positions:
                histories = histories_at(dataset, decision, session)
                for held_symbol, position in positions.items():
                    stop, reason = protective_exit(
                        position,
                        histories.get(held_symbol, []),
                        decision,
                        get_spec(position["version"]),
                    )
                    position["stop"] = max(position["stop"], stop)
                    if reason and fresh(books.get(held_symbol), decision):
                        pending[held_symbol] = {
                            "side": "sell",
                            "remaining": position["quantity"],
                            "price": books[held_symbol]["bid"],
                            "at": decision,
                            "reason": reason,
                        }
            if (
                decision
                >= session.closes
                - timedelta(minutes=VALIDATED_POLICY["entry_cutoff_minutes"])
                or day_halt
                or peak_halt
                or observation["issues"]
            ):
                continue
            weights = observation[mode + "_weights"]
            for signal in rank(
                [Signal(**s) for s in observation["signals"]],
                weights,
                set(positions) | set(pending),
            ):
                if (
                    len(set(positions) | set(pending))
                    >= VALIDATED_POLICY["max_positions"]
                ):
                    break
                book, trade = books.get(signal.symbol), ticks.get(signal.symbol)
                if not fresh(book, decision) or not fresh(trade, decision):
                    continue
                price = book["ask"]
                if (
                    not signal.stop < price < signal.target
                    or abs(price / signal.price - 1) > 0.005
                ):
                    continue
                net = signal.target - price - costs(price, signal.target)
                risk = price - signal.stop + costs(price, signal.stop)
                if net <= 0:
                    continue
                eq = equity()
                reserved_cash = sum(
                    p["remaining"] * p["price"] * (1 + ENTRY_COST_RATE)
                    for p in pending.values()
                    if p["side"] == "buy"
                )
                room = max(
                    0,
                    initial * VALIDATED_POLICY["daily_loss_limit"]
                    - max(0, initial - eq)
                    - risk_reserved(),
                )
                qty = math.floor(
                    min(
                        (cash - reserved_cash) / (price * (1 + ENTRY_COST_RATE)),
                        eq
                        * min(
                            VALIDATED_POLICY["symbol_cap"], weights[signal.strategy_id]
                        )
                        / (price * (1 + ENTRY_COST_RATE)),
                        min(eq * VALIDATED_POLICY["trade_risk"], room) / risk,
                    )
                )
                if qty > 0:
                    pending[signal.symbol] = {
                        "side": "buy",
                        "remaining": qty,
                        "price": price,
                        "risk": risk,
                        "signal": signal,
                        "at": decision,
                        "reason": "entry",
                    }
        if event["kind"] == "quote":
            books[symbol] = event
        else:
            ticks[symbol] = event
        eq = equity()
        peak = max(peak, eq)
        day_halt = day_halt or eq <= initial * (
            1 - VALIDATED_POLICY["daily_loss_limit"]
        )
        peak_halt = peak_halt or eq <= peak * (
            1 - VALIDATED_POLICY["peak_drawdown_limit"]
        )
        for s, order in list(pending.items()):
            if order["side"] == "buy" and (
                day_halt or peak_halt or now - order["at"] >= timedelta(minutes=1)
            ):
                missed += order["remaining"]
                del pending[s]
            elif (
                order["side"] == "sell"
                and now - order["at"] >= timedelta(minutes=1)
                and fresh(books.get(s), now)
            ):
                order.update(price=books[s]["bid"], at=now)
        book = books.get(symbol)
        if not fresh(book, now):
            continue
        if event["kind"] == "tick":
            comparisons += 1
        p = positions.get(symbol)
        if p and (symbol not in pending or pending[symbol]["side"] == "buy"):
            reason = (
                "close"
                if now
                >= session.closes
                - timedelta(minutes=VALIDATED_POLICY["liquidation_minutes"])
                else (
                    "stop"
                    if book["bid"] <= p["stop"]
                    else (
                        "target"
                        if book["bid"] >= p["target"]
                        else (
                            "time_limit"
                            if now - p["opened"]
                            >= timedelta(
                                minutes=get_spec(p["version"]).max_hold_minutes
                            )
                            else None
                        )
                    )
                )
            )
            if reason:
                if symbol in pending:
                    missed += pending[symbol]["remaining"]
                pending[symbol] = {
                    "side": "sell",
                    "remaining": p["quantity"],
                    "price": book["bid"],
                    "at": now,
                    "reason": reason,
                }
        order = pending.get(symbol)
        if (
            event["kind"] != "tick"
            or not order
            or now <= order["at"] + timedelta(seconds=1)
            or stamp(event["event_at"]) <= order["at"] + timedelta(seconds=1)
            or not fresh(event, now)
        ):
            continue
        crossed = (
            event["price"] < order["price"]
            if order["side"] == "buy"
            else event["price"] > order["price"]
        )
        if not crossed:
            continue
        capacity = math.floor(
            min(
                event["quantity"] * 0.01,
                book["ask_size" if order["side"] == "buy" else "bid_size"],
            )
        )
        qty = min(order["remaining"], capacity)
        if qty <= 0:
            continue
        if qty < order["remaining"]:
            partials += 1
        price = order["price"]
        if order["side"] == "buy":
            signal = order["signal"]
            debit = qty * price * (1 + ENTRY_COST_RATE)
            if debit > cash:
                continue
            cash -= debit
            position = positions.setdefault(
                symbol,
                {
                    "quantity": 0,
                    "entry_price": price,
                    "basis": 0,
                    "initial_quantity": 0,
                    "net_proceeds": 0,
                    "stop": signal.stop,
                    "target": signal.target,
                    "opened": now,
                    "version": signal.version,
                    "strategy_id": signal.strategy_id,
                    "risk": order["risk"],
                },
            )
            position["quantity"] += qty
            position["initial_quantity"] += qty
            position["basis"] += debit
        else:
            position = positions[symbol]
            proceeds = qty * price * (1 - EXIT_COST_RATE)
            cash += proceeds
            position["quantity"] -= qty
            position["net_proceeds"] += proceeds
            if not position["quantity"]:
                trades.append(
                    {
                        "symbol": symbol,
                        "strategy_id": position["strategy_id"],
                        "quantity": position["initial_quantity"],
                        "net_pnl": position["net_proceeds"] - position["basis"],
                        "reason": order["reason"],
                        "entry_at": position["opened"].isoformat(),
                        "exit_at": now.isoformat(),
                    }
                )
                del positions[symbol]
        order["remaining"] -= qty
        if not order["remaining"]:
            del pending[symbol]
    return {
        "net_pnl": cash - initial if not positions else None,
        "ending_equity": equity(),
        "peak": peak,
        "round_trips": len(trades),
        "trades": trades,
        "partial_fills": partials,
        "missed_quantity": missed,
        "unresolved": sorted(positions),
        "execution_comparisons": comparisons,
        "drawdown_halted": peak_halt,
        "fill_model": "strict trade-through; 1% tape participation; quoted-size bound; 1s latency; 10bps per side",
    }


def finalize(experiment, dataset, session, now):
    if now < session.closes:
        raise ValueError("shadow_session_not_closed")
    day = session.opens.astimezone(KST).date().isoformat()
    candidates = experiment.records("frozen")
    observations = [
        r for r in experiment.records("shadow_observation") if r["day"] == day
    ]
    observations.sort(key=lambda r: stamp(r["at"]))
    issues = set(issue for r in observations for issue in r["issues"])
    stamps = [stamp(r["at"]) for r in observations]
    if (
        not stamps
        or stamps[0] > session.opens + timedelta(minutes=1)
        or stamps[-1] < session.closes - timedelta(minutes=1)
        or any(b - a > timedelta(minutes=2) for a, b in zip(stamps, stamps[1:]))
    ):
        issues.add("incomplete_realtime_session_coverage")
    mismatches = 0
    for observation in observations:
        expected, _ = signals_at(dataset, candidates, stamp(observation["at"]), session)
        if digest([asdict(s) for s in expected]) != digest(observation["signals"]):
            mismatches += 1
    events = [
        e
        for e in dataset["events"]
        if session.opens <= stamp(e["received_at"]) < session.closes
    ]
    previous = [
        r
        for r in experiment.records("shadow")
        if r["day"] < day and any(r["candidate_hash"] == digest(c) for c in candidates)
    ]
    previous = max(previous, key=lambda r: r["day"]) if previous else None
    paired = {}
    for mode in ("rules", "ai"):
        prior = previous["paired_rules_ai"][mode] if previous else {}
        paired[mode] = simulate(
            observations,
            events,
            session,
            mode,
            prior.get("ending_equity", VALIDATED_POLICY["initial_capital"]),
            prior.get("peak"),
            halted=prior.get("drawdown_halted", False),
            dataset=dataset,
        )
    if any(r["unresolved"] for r in paired.values()):
        issues.add("unresolved_tick_simulation_positions")
    if any(r["drawdown_halted"] for r in paired.values()):
        issues.add("shadow_drawdown_limit")
    for candidate in candidates:
        if any(
            r["candidate_hashes"].get(candidate["strategy_id"]) != digest(candidate)
            for r in observations
        ):
            issues.add("frozen_candidate_changed")
        result = {
            "day": day,
            "version": candidate["version"],
            "candidate_hash": digest(candidate),
            "started_at": stamps[0].isoformat() if stamps else now.isoformat(),
            "completed": True,
            "data_mode": dataset["data_mode"],
            "signal_mismatches": mismatches,
            "quality_issues": sorted(issues),
            "quote_observations": sum(e["kind"] == "quote" for e in events),
            "tick_observations": sum(e["kind"] == "tick" for e in events),
            "execution_comparisons": paired["rules"]["execution_comparisons"],
            "orders_submitted": 0,
            "paired_rules_ai": paired,
            "dataset_hash": dataset["sha256"],
        }
        experiment.append("shadow", candidate["strategy_id"] + ":" + day, result, now)
    return {
        "day": day,
        "quality_issues": sorted(issues),
        "signal_mismatches": mismatches,
        "orders_submitted": 0,
    }
