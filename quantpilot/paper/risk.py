"""Fresh deterministic experiment risk checks, independent from AI/lifecycle levels."""

from __future__ import annotations
import math
from datetime import datetime
from quantpilot.paper.config import aware
from quantpilot.paper.calendar import KST


def data_quarantined(store, symbol, now):
    quarantine = store.get("data_quarantine:" + symbol)
    return bool(quarantine and quarantine["day"] == now.astimezone(KST).date().isoformat())


def fresh_quote(quote, now, ttl):
    aware(now)
    aware(quote.as_of)
    numbers = (quote.last, quote.bid, quote.ask)
    if any(v is None or not math.isfinite(v) or v <= 0 for v in numbers):
        raise ValueError("quote_incomplete")
    if quote.bid > quote.ask or not 0 <= (now - quote.as_of).total_seconds() < ttl:
        raise ValueError("quote_stale_or_crossed")
    return quote


def tick(price):
    for ceiling, step in (
        (2000, 1),
        (5000, 5),
        (20000, 10),
        (50000, 50),
        (200000, 100),
        (500000, 500),
    ):
        if price < ceiling:
            return step
    return 1000


def limit_price(quote, side):
    price = quote.ask if side == "buy" else quote.bid
    if price is None or not math.isfinite(price) or price <= 0:
        raise ValueError("invalid_price")
    step = tick(price)
    return int(
        (math.ceil(price / step) if side == "buy" else math.floor(price / step)) * step
    )


def entry_size(store, signal, quote, weight, now, *, trial=False, ignore_order_id=None):
    policy = store.policy
    if data_quarantined(store, signal.symbol, now):
        return 0
    if signal.strategy_id not in policy.active_strategies:
        from quantpilot.paper.research import candidates

        eligible = (
            [c for c in candidates(store) if c.stage == "paper_trial"]
            if policy.research_enabled
            else []
        )
        if (
            len(eligible) != 1
            or eligible[0].strategy_id != signal.strategy_id
            or eligible[0].version != getattr(signal, "version", None)
        ):
            return 0
        trial = True
        weight = min(weight, eligible[0].allocation_cap, 0.1)
    fresh_quote(quote, now, policy.quote_ttl_seconds)
    if quote.symbol != signal.symbol:
        raise ValueError("quote_symbol_mismatch")
    values = (signal.price, signal.stop, signal.target, signal.score, weight)
    if any(not math.isfinite(x) for x in values):
        raise ValueError("nonfinite_signal")
    if (
        not 0 < signal.stop < signal.price < signal.target
        or not 0 <= weight <= policy.strategy_cap
    ):
        raise ValueError("invalid_signal_or_weight")
    positions = store.positions()
    pending = [o for o in store.orders(True) if o["id"] != ignore_order_id]
    if store.get("control") != "running":
        return 0
    occupied = {p["symbol"] for p in positions} | {
        o["symbol"] for o in pending if o["side"] == "buy"
    }
    if signal.symbol in occupied or len(occupied) >= policy.max_positions:
        return 0
    if any(o["state"] in {"outcome_unknown", "cancel_unknown"} for o in pending):
        return 0
    # Reserve unrealized losses and quarantined capital; never borrow account cash.
    marks = store.get("marks", {})
    exposure = sum(
        p["quantity"] * marks.get(p["symbol"], p["basis"] / p["quantity"])
        for p in positions
    )
    equity = max(
        0.0,
        min(
            store.get("cash") + exposure,
            store.get("initial_capital") + store.get("realized"),
        ),
    )
    price = limit_price(quote, "buy")
    if (
        price >= signal.target
        or price <= signal.stop
        or abs(price / signal.price - 1) > 0.005
    ):
        return 0
    reserve = sum(
        (o["quantity"] - o["filled"]) * o["price"] * (1 + policy.fee_bps / 10000)
        for o in pending
        if o["side"] == "buy"
    )
    strategy_used = sum(
        p["basis"] for p in positions if p["strategy"] == signal.strategy_id
    )
    strategy_used += sum(
        (o["quantity"] - o["filled"]) * o["price"]
        for o in pending
        if o["strategy"] == signal.strategy_id and o["side"] == "buy"
    )
    cap = min(weight, policy.strategy_cap, 0.10 if trial else policy.strategy_cap)
    cash_budget = max(
        0.0,
        min(
            store.get("cash") - reserve,
            equity * policy.symbol_cap,
            equity * cap - strategy_used,
        ),
    )
    roundtrip = (
        2 * policy.fee_bps + policy.sell_tax_bps + 2 * policy.slippage_bps
    ) / 10000
    risk_budget = equity * policy.trade_risk
    if policy.strategy_generation != "intraday_v2":
        # The daily-loss and peak-drawdown budget gates every generation; the
        # intraday_v2 branch below applies the same budget with its extra gates.
        from quantpilot.paper.intraday.controls import loss_budget

        budget = loss_budget(store, now, ignore_order_id)
        if not budget["available"]:
            return 0
        risk_budget = min(risk_budget, budget["available"])
    if policy.strategy_generation == "intraday_v2":
        from quantpilot.paper.intraday.controls import (
            feed_fresh,
            loss_budget,
            universe_current,
        )
        from quantpilot.paper.intraday.deployment import admitted

        if not admitted(
            store, signal.strategy_id, getattr(signal, "version", None)
        ) or not feed_fresh(store, now, signal.symbol):
            return 0
        if store.get("incident") or not universe_current(store, signal.symbol, now):
            return 0
        mark_times = store.get("marks_at", {})
        for position in positions:
            mark_at = datetime.fromisoformat(
                mark_times.get(position["symbol"], position["opened"])
            )
            if not 0 <= (now - mark_at).total_seconds() < policy.quote_ttl_seconds:
                return 0
        # Use an executable target tick and charge both legs before ranking or sizing.
        target = math.floor(signal.target / tick(signal.target)) * tick(signal.target)
        net_target = target * (
            1 - (policy.fee_bps + policy.sell_tax_bps + policy.slippage_bps) / 10000
        ) - price * (1 + (policy.fee_bps + policy.slippage_bps) / 10000)
        if net_target <= 0:
            return 0
        budget = loss_budget(store, now, ignore_order_id)
        risk_budget = min(
            risk_budget, budget["equity"] * policy.trade_risk, budget["available"]
        )
        cash_budget = max(
            0,
            min(
                cash_budget,
                budget["equity"] * policy.symbol_cap,
                budget["equity"] * cap - strategy_used,
            ),
        )
    per_share_risk = price - signal.stop + price * roundtrip
    return max(
        0,
        math.floor(
            min(
                cash_budget / (price * (1 + policy.fee_bps / 10000)),
                risk_budget / per_share_risk,
            )
        ),
    )


def sell_quantity(store, symbol, quote, now):
    fresh_quote(quote, now, store.policy.quote_ttl_seconds)
    if quote.symbol != symbol:
        raise ValueError("quote_symbol_mismatch")
    p = next((p for p in store.positions() if p["symbol"] == symbol), None)
    if not p or any(o["symbol"] == symbol for o in store.orders(True)):
        return 0
    return p["quantity"]


def authorize_order(store, order, quote, now, session):
    """Final independent authorization, including the already-reserved intent."""
    from types import SimpleNamespace
    from datetime import timedelta

    policy = store.policy
    fresh_quote(quote, now, policy.quote_ttl_seconds)
    if not session or not session.trading(now):
        raise ValueError("exchange_closed")
    if order["policy_version"] != policy.version or quote.symbol != order["symbol"]:
        raise ValueError("order_identity_changed")
    if type(order["quantity"]) is not int or order["quantity"] <= 0:
        raise ValueError("quantity_invalid")
    if order["side"] == "buy":
        if now >= session.closes - timedelta(minutes=policy.entry_cutoff_minutes):
            raise ValueError("entry_window_closed")
        s = SimpleNamespace(
            symbol=order["symbol"],
            strategy_id=order["strategy"],
            version=order["version"],
            price=order["price"],
            stop=order["stop"],
            target=order["target"],
            score=1.0,
        )
        weight = store.get("weights", {}).get(order["strategy"], 0)
        if order["quantity"] > entry_size(
            store, s, quote, weight, now, ignore_order_id=order["id"]
        ):
            raise ValueError("final_entry_risk_failed")
    elif order["side"] == "sell":
        p = next((p for p in store.positions() if p["symbol"] == order["symbol"]), None)
        if (
            not p
            or p["strategy"] != order["strategy"]
            or p["version"] != order["version"]
            or order["quantity"] > p["quantity"]
        ):
            raise ValueError("final_sell_attribution_failed")
        if any(
            o["id"] != order["id"] and o["symbol"] == order["symbol"]
            for o in store.orders(True)
        ):
            raise ValueError("conflicting_order")
    else:
        raise ValueError("unsupported_side")
    store.audit(
        "final_risk_passed",
        {
            "order_id": order["id"],
            "policy_version": policy.version,
            "checks": [
                "fresh_quote",
                "session_window",
                "attribution",
                "capital_reservations",
                "control_state",
            ],
        },
        now,
    )
