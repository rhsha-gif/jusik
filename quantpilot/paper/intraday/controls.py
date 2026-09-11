"""Persistent risk budgets shared by the final order gate and runtime."""

from datetime import datetime
import math

from quantpilot.paper.calendar import KST
from quantpilot.paper.config import aware


def loss_budget(store, now, ignore_order_id=None):
    aware(now)
    policy = store.policy
    with store.transaction():
        positions = store.positions()
        marks = store.get("marks", {})
        slip = policy.slippage_bps / 10000
        exit_cost = (policy.fee_bps + policy.sell_tax_bps + policy.slippage_bps) / 10000
        modeled_slip = store.db.execute(
            "SELECT COALESCE(SUM(pnl-adjusted_pnl),0) FROM trades"
        ).fetchone()[0]
        equity = (
            store.get("cash")
            - modeled_slip
            + sum(
                p["quantity"]
                * marks.get(p["symbol"], p["basis"] / p["quantity"])
                * (1 - exit_cost)
                - p["basis"] * slip
                for p in positions
            )
        )
        if not math.isfinite(equity) or equity < 0:
            raise ValueError("intraday_equity_invalid")
        day = now.astimezone(KST).date().isoformat()
        state = store.get("intraday_loss_state", {})
        if state and day < state["day"]:
            raise ValueError("intraday_risk_clock_reversed")
        first = not state
        if state.get("day") != day:
            base = (
                store.get("day_base", store.get("initial_capital"))
                if first
                else state.get("equity", equity)
            )
            state = {**state, "day": day, "day_base": base, "daily_halted": False}
        peak = max(state.get("peak", store.get("initial_capital")), equity)
        daily_loss = max(0, state["day_base"] - equity)
        daily_halted = (
            state.get("daily_halted", False)
            or daily_loss >= state["day_base"] * policy.daily_loss_limit
        )
        drawdown_halted = state.get("drawdown_halted", False) or equity <= peak * (
            1 - policy.peak_drawdown_limit
        )
        pending = [
            o
            for o in store.orders(True)
            if o["id"] != ignore_order_id and o["side"] == "buy"
        ]
        roundtrip = (
            2 * policy.fee_bps + policy.sell_tax_bps + 2 * policy.slippage_bps
        ) / 10000
        reserved = sum(
            max(
                0,
                p["basis"] / (1 + policy.fee_bps / 10000) * (1 + roundtrip)
                - p["quantity"] * p["stop"],
            )
            for p in positions
        )
        reserved += sum(
            (o["quantity"] - o["filled"])
            * max(0, o["price"] - o["stop"] + o["price"] * roundtrip)
            for o in pending
        )
        available = max(
            0, state["day_base"] * policy.daily_loss_limit - daily_loss - reserved
        )
        if daily_halted or drawdown_halted:
            available = 0
        updated = dict(
            state,
            peak=peak,
            equity=equity,
            reserved=reserved,
            available=available,
            daily_halted=daily_halted,
            drawdown_halted=drawdown_halted,
        )
        if (daily_halted and not state.get("daily_halted")) or (
            drawdown_halted and not state.get("drawdown_halted")
        ):
            store.audit("intraday_loss_halt", updated, now)
        store.put("intraday_loss_state", updated)
        return updated


def feed_fresh(store, now, symbol=None):
    value = store.get("intraday_feed_at")
    try:
        stamp = aware(datetime.fromisoformat(value))
        if not 0 <= (now - stamp).total_seconds() < store.policy.quote_ttl_seconds:
            return False
        if symbol is not None:
            for kind in ("tick", "quote"):
                event = store.get("intraday_event:" + symbol + ":" + kind, {})
                received = aware(datetime.fromisoformat(event["received_at"]))
                occurred = aware(datetime.fromisoformat(event["event_at"]))
                if (
                    not 0
                    <= (now - received).total_seconds()
                    < store.policy.quote_ttl_seconds
                    or not 0
                    <= (now - occurred).total_seconds()
                    < store.policy.quote_ttl_seconds
                ):
                    return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def universe_current(store, symbol, now):
    from quantpilot.paper.intraday.universe import eligible_symbols

    snapshot = store.get("intraday_universe")
    try:
        at = aware(datetime.fromisoformat(snapshot["at"]))
        if not 0 <= (now - at).total_seconds() <= 300:
            return False
        symbols, _ = eligible_symbols(snapshot, now)
        return symbol in symbols
    except (KeyError, TypeError, ValueError):
        return False
