"""Per-order latency timeline and measurement-only audits.

Everything here records; nothing here gates. The timeline links one order id to
the instants the diagnosis asked for (bar close, local observation, signal
computation, decision, queue exit / send start, broker response, fill
recognition; for exits the first observation of the exit condition). Stages are
first-write-wins so a retried callback never rewrites an earlier observation.

The measurement-only audits (``entry_net_target``, ``reentry_after_stop``,
``protective_sell_reissued``) expose the diagnosis's open questions without
changing any order: they are inputs to the next protocol decision, not gates.
"""

from __future__ import annotations

from datetime import datetime
import json
import math

from quantpilot.paper.calendar import KST

# A signal is judged on the completed bar that closed at ``bar_end``. The runtime
# already refuses bars whose start is more than 150 s old, so from the close the
# same freshness gate is 90 s. Anything older must not become an order.
SIGNAL_MAX_AGE_SECONDS = 90

ENTRY_STAGES = (
    "bar_end",
    "bar_observed_at",
    "signal_computed_at",
    "decision_at",
    "prepared_at",
    "queue_enter_at",
    "send_start_at",
    "response_at",
    "failed_at",
    "fill_recognized_at",
)
EXIT_STAGES = (
    "condition_first_observed_at",
    "quote_observed_at",
    "decision_at",
    "prepared_at",
    "queue_enter_at",
    "send_start_at",
    "response_at",
    "failed_at",
    "fill_recognized_at",
)
# (label, from stage, to stage). Headline rows first.
ENTRY_INTERVALS = (
    ("bar_close_to_send", "bar_end", "send_start_at"),
    ("bar_close_to_observed", "bar_end", "bar_observed_at"),
    ("observed_to_computed", "bar_observed_at", "signal_computed_at"),
    ("computed_to_decision", "signal_computed_at", "decision_at"),
    ("decision_to_send", "decision_at", "send_start_at"),
    ("send_to_response", "send_start_at", "response_at"),
    ("decision_to_fill_recognized", "decision_at", "fill_recognized_at"),
)
EXIT_INTERVALS = (
    ("condition_to_send", "condition_first_observed_at", "send_start_at"),
    ("condition_to_decision", "condition_first_observed_at", "decision_at"),
    ("decision_to_send", "decision_at", "send_start_at"),
    ("send_to_response", "send_start_at", "response_at"),
    ("condition_to_fill_recognized", "condition_first_observed_at", "fill_recognized_at"),
)


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def stale_signal(signal, now) -> bool:
    """True when the signal's bar closed longer ago than the freshness gate allows."""
    bar_end = getattr(signal, "bar_end", None)
    if bar_end is None:
        return False
    return (now - bar_end).total_seconds() > SIGNAL_MAX_AGE_SECONDS


def mark(store, order_id, **stages):
    """Record stages on ``timeline:<order_id>``; an existing stage is never overwritten."""
    key = "timeline:" + order_id
    current = store.get(key) or {}
    changed = False
    for stage, value in stages.items():
        if value is None or stage in current:
            continue
        current[stage] = _iso(value)
        changed = True
    if changed:
        store.put(key, current)
    return current


def link_signal(store, order_id, signal, decision_at):
    """Copy a signal's provenance onto the entry order's timeline at decision time."""
    return mark(
        store,
        order_id,
        side="buy",
        symbol=signal.symbol,
        strategy=signal.strategy_id,
        bar_end=getattr(signal, "bar_end", None),
        bar_observed_at=getattr(signal, "observed_at", None),
        signal_computed_at=getattr(signal, "computed_at", None),
        decision_at=decision_at,
    )


def observe_exit_condition(store, symbol, reason, at):
    """Remember when a protective exit condition was first seen for a held symbol.

    Returns the first observation instant (ISO) while the condition persists and
    clears the record once the condition is gone, so the next episode starts fresh.
    """
    key = "exit_condition:" + symbol
    if not reason:
        if store.get(key) is not None:
            store.put(key, None)
        return None
    current = store.get(key)
    if current and current.get("reason") == reason:
        return current["at"]
    store.put(key, {"reason": reason, "at": at.isoformat()})
    return at.isoformat()


def link_exit(store, order_id, symbol, strategy, reason, quote, decision_at):
    """Stamp the exit order with the first observation, then start the next episode fresh.

    The protection loop only visits held symbols, so after the sell fills nobody would
    clear the record; a later re-entry must not inherit this episode's observation.
    """
    first = (store.get("exit_condition:" + symbol) or {}).get("at")
    store.put("exit_condition:" + symbol, None)
    return mark(
        store,
        order_id,
        side="sell",
        symbol=symbol,
        strategy=strategy,
        reason=reason,
        condition_first_observed_at=first,
        quote_observed_at=getattr(quote, "as_of", None),
        decision_at=decision_at,
    )


def measure_entry(store, signal, price, quantity, now):
    """Measurement-only audits for a reserved entry: net target and same-day re-entry."""
    from quantpilot.paper.risk import net_target_per_share, roundtrip_cost_ratio

    policy = store.policy
    net_target = net_target_per_share(policy, price, signal.target)
    planned_risk = price - signal.stop + price * roundtrip_cost_ratio(policy)
    store.audit(
        "entry_net_target",
        {
            "symbol": signal.symbol,
            "strategy": signal.strategy_id,
            "price": price,
            "stop": signal.stop,
            "target": signal.target,
            "quantity": quantity,
            "net_target_per_share": round(net_target, 4),
            "planned_risk_per_share": round(planned_risk, 4),
            "net_reward_ratio": round(net_target / planned_risk, 4) if planned_risk > 0 else None,
            "would_pass_net_target_gate": net_target > 0,
        },
        now,
    )
    day = now.astimezone(KST).date().isoformat()
    prior_stops = [
        o["id"]
        for o in store.orders()
        if o["symbol"] == signal.symbol
        and o["side"] == "sell"
        and o["reason"] == "stop"
        and o["filled"] > 0
        and datetime.fromisoformat(o["at"]).astimezone(KST).date().isoformat() == day
    ]
    if prior_stops:
        store.audit(
            "reentry_after_stop",
            {"symbol": signal.symbol, "strategy": signal.strategy_id,
             "prior_stop_orders": prior_stops},
            now,
        )


def measure_exit(store, order_id, symbol, reason, now):
    """Measurement-only audit: how many protective sells this symbol already had today."""
    day = now.astimezone(KST).date().isoformat()
    prior = [
        o["id"]
        for o in store.orders()
        if o["symbol"] == symbol
        and o["side"] == "sell"
        and o["id"] != order_id
        and datetime.fromisoformat(o["at"]).astimezone(KST).date().isoformat() == day
    ]
    if prior:
        store.audit(
            "protective_sell_reissued",
            {"symbol": symbol, "reason": reason, "order_id": order_id,
             "prior_sells_today": len(prior)},
            now,
        )


def _percentile(values, ratio):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(ratio * len(ordered)) - 1))
    return round(ordered[index], 3)


def _seconds(timeline, start, end):
    try:
        a = datetime.fromisoformat(timeline[start])
        b = datetime.fromisoformat(timeline[end])
    except (KeyError, TypeError, ValueError):
        return None
    delta = (b - a).total_seconds()
    return delta if math.isfinite(delta) else None


def timelines(view, day=None):
    """All ``timeline:*`` records, optionally only those decided on one KST day."""
    rows = view.db.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'timeline:%'"
    ).fetchall()
    out = []
    for key, value in rows:
        try:
            record = json.loads(value)
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        record = dict(record, order_id=key.split(":", 1)[1])
        if day:
            decided = record.get("decision_at")
            try:
                if datetime.fromisoformat(decided).astimezone(KST).date().isoformat() != day:
                    continue
            except (TypeError, ValueError):
                continue
        out.append(record)
    return out


def report(view, day=None, now=None):
    """p50/p95/p99 per stage interval for entries and exits, with missing counts."""
    if day is None and now is not None:
        day = now.astimezone(KST).date().isoformat()
    records = timelines(view, day)
    result = {"day": day, "orders": len(records), "entry": {}, "exit": {},
              "targets_seconds": {"exit_condition_to_send": 5, "entry_bar_close_to_send": 30},
              "note": "Stage instants are local observations; broker fill times are not measured."}
    for side, intervals, bucket in (("buy", ENTRY_INTERVALS, "entry"), ("sell", EXIT_INTERVALS, "exit")):
        subset = [r for r in records if r.get("side") == side]
        for label, start, end in intervals:
            values = [v for v in (_seconds(r, start, end) for r in subset) if v is not None]
            result[bucket][label] = {
                "count": len(values),
                "missing": len(subset) - len(values),
                "p50": _percentile(values, 0.50),
                "p95": _percentile(values, 0.95),
                "p99": _percentile(values, 0.99),
                "max": round(max(values), 3) if values else None,
            }
        result[bucket]["orders"] = len(subset)
    return result


def headline(view, now):
    """Compact status fields: today's headline p50/p95 for entry and exit."""
    full = report(view, now=now)
    entry = full["entry"].get("bar_close_to_send", {})
    exit_ = full["exit"].get("condition_to_send", {})
    return {
        "day": full["day"],
        "entry_bar_close_to_send": {k: entry.get(k) for k in ("count", "p50", "p95")},
        "exit_condition_to_send": {k: exit_.get(k) for k in ("count", "p50", "p95")},
    }
