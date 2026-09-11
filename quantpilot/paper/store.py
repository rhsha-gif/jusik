"""Experiment ledger. Durable intent precedes transport; cumulative fills replay once."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quantpilot.paper.config import Policy, aware

OPEN = (
    "prepared",
    "submitted",
    "accepted",
    "partially_filled",
    "outcome_unknown",
    "cancel_unknown",
)
TERMINAL = (
    "filled",
    "cancelled",
    "rejected",
    "expired_pre_dispatch",
    "failed_pre_dispatch",
)


def encode(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, default=lambda v: v.isoformat()
    )


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            """
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, symbol TEXT NOT NULL, strategy TEXT NOT NULL,
          side TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL, stop REAL NOT NULL, target REAL NOT NULL,
          version TEXT NOT NULL, policy_version INTEGER NOT NULL, state TEXT NOT NULL, at TEXT NOT NULL,
          filled INTEGER NOT NULL DEFAULT 0, amount REAL NOT NULL DEFAULT 0, cost REAL NOT NULL DEFAULT 0,
          broker_reference TEXT, reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS positions(symbol TEXT PRIMARY KEY, strategy TEXT NOT NULL, quantity INTEGER NOT NULL,
          basis REAL NOT NULL, stop REAL NOT NULL, target REAL NOT NULL, version TEXT NOT NULL,
          opened TEXT NOT NULL, quarantined INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS trades(id TEXT PRIMARY KEY, strategy TEXT NOT NULL, symbol TEXT NOT NULL,
          quantity INTEGER NOT NULL, pnl REAL NOT NULL, adjusted_pnl REAL NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bars(symbol TEXT NOT NULL, start TEXT NOT NULL, body TEXT NOT NULL,
          PRIMARY KEY(symbol,start));
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
          at TEXT NOT NULL, result TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, text TEXT NOT NULL, state TEXT NOT NULL,
          at TEXT NOT NULL, error TEXT);
        CREATE TABLE IF NOT EXISTS lab(id TEXT PRIMARY KEY, body TEXT NOT NULL);
        """
        )
        if "entry_atr14" not in {
            r[1] for r in self.db.execute("PRAGMA table_info(orders)")
        }:
            self.db.execute("ALTER TABLE orders ADD COLUMN entry_atr14 REAL")
        if "gross_pnl" not in {
            r[1] for r in self.db.execute("PRAGMA table_info(trades)")
        }:
            self.db.execute("ALTER TABLE trades ADD COLUMN gross_pnl REAL")
        with self.transaction():
            if self.get("policy") is None:
                self.put("policy", Policy().model_dump(mode="json"))
                self.put("policy:1", Policy().model_dump(mode="json"))
                self.put("control", "stopped")
                self.put("cash", 5_000_000.0)
                self.put("realized", 0.0)
                self.put("initial_capital", 5_000_000.0)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            yield
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def get(self, key, default=None):
        row = self.db.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, encode(value)),
        )

    def audit(self, kind, payload, now=None):
        self.db.execute(
            "INSERT INTO audit(at,kind,payload) VALUES(?,?,?)",
            ((now or datetime.now(timezone.utc)).isoformat(), kind, encode(payload)),
        )

    @property
    def policy(self):
        return Policy.model_validate(self.get("policy"))

    def configure(self, changes: dict, expected_version: int):
        with self.transaction():
            current = self.policy
            if current.version != expected_version:
                raise ValueError("policy_version_conflict")
            if any(k in changes for k in ("version", "initial_capital")):
                raise ValueError("immutable_policy_field")
            if current.strategy_generation == "intraday_v2" and self.get(
                "intraday_admission"
            ):
                from quantpilot.paper.intraday.deployment import VALIDATED_POLICY

                if any(
                    k in VALIDATED_POLICY and v != getattr(current, k)
                    for k, v in changes.items()
                ):
                    raise ValueError("admitted_execution_policy_immutable")
            if (
                "strategy_generation" in changes
                and changes["strategy_generation"] != current.strategy_generation
            ):
                if (
                    current.strategy_generation == "intraday_v2"
                    or self.orders()
                    or self.get("control") != "stopped"
                ):
                    raise ValueError("strategy_generation_requires_new_experiment")
            if "data_mode" in changes and (
                self.orders() or self.get("control") != "stopped"
            ):
                raise ValueError("mode_change_requires_empty_stopped_ledger")
            updated = Policy.model_validate(
                current.model_dump() | changes | {"version": current.version + 1}
            )
            self.put("policy:" + str(current.version), current.model_dump(mode="json"))
            self.put("policy:" + str(updated.version), updated.model_dump(mode="json"))
            self.put("policy", updated.model_dump(mode="json"))
            self.audit(
                "policy_changed", {"version": updated.version, "changes": changes}
            )
            return updated

    def review_intraday_halt(self, reason, now):
        """Explicit operator review acknowledges a drawdown epoch; never starts orders."""
        from quantpilot.paper.intraday.controls import loss_budget

        if not isinstance(reason, str) or len(reason.strip()) < 10:
            raise ValueError("drawdown_review_reason_required")
        with self.transaction():
            if (
                self.get("control") not in {"paused", "stopped"}
                or self.orders(True)
                or self.positions()
            ):
                raise ValueError("drawdown_review_requires_flat_paused_ledger")
            state = loss_budget(self, now)
            if not state["drawdown_halted"]:
                raise ValueError("no_drawdown_halt")
            self.audit(
                "intraday_drawdown_review", {"reason": reason, "previous": state}, now
            )
            state.update(drawdown_halted=False, peak=state["equity"])
            self.put("intraday_loss_state", state)

    def control(self, action):
        states = {
            "start": "running",
            "resume": "running",
            "pause": "paused",
            "flatten": "flattening",
        }
        if action not in states:
            raise ValueError("unknown_control")
        with self.transaction():
            if action == "pause" and self.get("control") == "flattening":
                self.audit("pause_during_flatten", {"flatten_pending": True})
                return
            if action in {"start", "resume"} and self.get("control") == "flattening":
                raise ValueError("flatten_in_progress")
            self.put("control", states[action])
            self.audit("control", {"action": action})

    def orders(self, open_only=False):
        rows = [dict(r) for r in self.db.execute("SELECT * FROM orders ORDER BY at,id")]
        return [r for r in rows if r["state"] in OPEN] if open_only else rows

    def positions(self):
        return [
            dict(r) for r in self.db.execute("SELECT * FROM positions ORDER BY symbol")
        ]

    def verify_cash(self):
        expected = float(self.policy.initial_capital)
        for order in self.orders():
            expected += (
                order["amount"] - order["cost"]
                if order["side"] == "sell"
                else -order["amount"] - order["cost"]
            )
        if not math.isfinite(expected) or not math.isclose(
            self.get("cash"), expected, rel_tol=0, abs_tol=0.01
        ):
            raise ValueError("experiment_cash_ledger_mismatch")
        if self.get("initial_capital") != self.policy.initial_capital:
            raise ValueError("experiment_initial_capital_mismatch")

    def reserve(
        self, *, order_id, signal, quantity, price, side, now, policy_version, reason
    ):
        with self.transaction():
            if self.db.execute(
                "SELECT 1 FROM orders WHERE id=?", (order_id,)
            ).fetchone():
                return False
            if self.policy.version != policy_version:
                raise ValueError("policy_version_conflict")
            if side == "buy" and self.get("control") != "running":
                raise ValueError("new_entries_paused")
            if any(o["symbol"] == signal.symbol for o in self.orders(True)):
                raise ValueError("symbol_has_pending_order")
            self.db.execute(
                "INSERT INTO orders(id,symbol,strategy,side,quantity,price,stop,target,version,policy_version,state,at,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    order_id,
                    signal.symbol,
                    signal.strategy_id,
                    side,
                    quantity,
                    price,
                    signal.stop,
                    signal.target,
                    signal.version,
                    policy_version,
                    "prepared",
                    aware(now).isoformat(),
                    reason,
                ),
            )
            self.audit(
                "order_reserved",
                {"id": order_id, "side": side, "quantity": quantity, "price": price},
                now,
            )
            self.db.execute(
                "UPDATE orders SET entry_atr14=? WHERE id=?",
                (getattr(signal, "entry_atr14", None), order_id),
            )
            return True

    def update_order(self, order_id, state, filled, amount, now, reference=None):
        """A broker-verified cumulative fill is the ONLY cash/position mutation source."""
        if (
            state not in (*OPEN, *TERMINAL)
            or type(filled) is not int
            or filled < 0
            or not math.isfinite(amount)
            or amount < 0
        ):
            raise ValueError("invalid_broker_fill")
        aware(now)
        with self.transaction():
            row = self.db.execute(
                "SELECT * FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown_local_order")
            order = dict(row)
            if (
                filled < order["filled"]
                or filled > order["quantity"]
                or amount < order["amount"]
            ):
                raise ValueError("nonmonotonic_fill")
            if (filled == order["filled"] and amount != order["amount"]) or (
                filled > order["filled"] and amount <= order["amount"]
            ):
                raise ValueError("inconsistent_fill_amount")
            if state == "filled" and filled != order["quantity"]:
                raise ValueError("incomplete_filled_order")
            if order["state"] in TERMINAL and (
                state != order["state"] or filled != order["filled"]
            ):
                raise ValueError("terminal_order_changed")
            delta, notional = filled - order["filled"], amount - order["amount"]
            policy = Policy.model_validate(
                self.get(
                    "policy:" + str(order["policy_version"]), self.policy.model_dump()
                )
            )
            # These are modeled costs; broker gross fills are preserved separately.
            cost = (
                notional
                * (
                    policy.fee_bps
                    + (policy.sell_tax_bps if order["side"] == "sell" else 0)
                )
                / 10000
            )
            if delta:
                p = self.db.execute(
                    "SELECT * FROM positions WHERE symbol=?", (order["symbol"],)
                ).fetchone()
                cash = self.get("cash")
                if order["side"] == "buy":
                    if p and (
                        p["strategy"] != order["strategy"]
                        or p["version"] != order["version"]
                    ):
                        raise ValueError("position_attribution_conflict")
                    if notional + cost > cash + 0.01:
                        raise ValueError("experiment_cash_exhausted")
                    qty, basis = (p["quantity"], p["basis"]) if p else (0, 0.0)
                    self.db.execute(
                        "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,0) ON CONFLICT(symbol) DO UPDATE SET quantity=excluded.quantity,basis=excluded.basis",
                        (
                            order["symbol"],
                            order["strategy"],
                            qty + delta,
                            basis + notional + cost,
                            order["stop"],
                            order["target"],
                            order["version"],
                            order["at"],
                        ),
                    )
                    self.put("cash", cash - notional - cost)
                else:
                    if (
                        not p
                        or p["strategy"] != order["strategy"]
                        or p["version"] != order["version"]
                        or delta > p["quantity"]
                    ):
                        raise ValueError("sell_exceeds_attributed_position")
                    released_basis = p["basis"] * delta / p["quantity"]
                    entry = self.db.execute(
                        "SELECT amount,cost FROM orders WHERE symbol=? AND side='buy' AND at=?",
                        (p["symbol"], p["opened"]),
                    ).fetchone()
                    if not entry or entry["amount"] <= 0:
                        raise ValueError("entry_cost_evidence_missing")
                    gross = notional - released_basis / (
                        1 + entry["cost"] / entry["amount"]
                    )
                    pnl = notional - cost - released_basis
                    adjusted = (
                        pnl - (notional + released_basis) * policy.slippage_bps / 10000
                    )
                    self.db.execute(
                        "INSERT INTO trades(id,strategy,symbol,quantity,pnl,adjusted_pnl,at,gross_pnl) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            f"{order_id}:{filled}",
                            order["strategy"],
                            order["symbol"],
                            delta,
                            pnl,
                            adjusted,
                            now.isoformat(),
                            gross,
                        ),
                    )
                    if delta == p["quantity"]:
                        self.db.execute(
                            "DELETE FROM positions WHERE symbol=?", (order["symbol"],)
                        )
                    else:
                        self.db.execute(
                            "UPDATE positions SET quantity=?,basis=? WHERE symbol=?",
                            (
                                p["quantity"] - delta,
                                p["basis"] - released_basis,
                                order["symbol"],
                            ),
                        )
                    self.put("cash", cash + notional - cost)
                    self.put("realized", self.get("realized", 0) + pnl)
            self.db.execute(
                "UPDATE orders SET state=?,filled=?,amount=?,cost=cost+?,broker_reference=COALESCE(?,broker_reference) WHERE id=?",
                (state, filled, amount, cost, reference, order_id),
            )
            self.audit(
                "order_reconciled",
                {"id": order_id, "state": state, "filled": filled},
                now,
            )

    def enqueue(self, key, text, now):
        self.db.execute(
            "INSERT OR IGNORE INTO outbox VALUES(?,?, 'pending',?,NULL)",
            (key, text, now.isoformat()),
        )

    def save_bars(self, bars):
        from dataclasses import asdict

        with self.transaction():
            for b in bars:
                body = encode(asdict(b))
                old = self.db.execute(
                    "SELECT body FROM bars WHERE symbol=? AND start=?",
                    (b.symbol, b.start.isoformat()),
                ).fetchone()
                if old and old[0] != body:
                    raise ValueError("completed_bar_revised")
                self.db.execute(
                    "INSERT OR IGNORE INTO bars VALUES(?,?,?)",
                    (b.symbol, b.start.isoformat(), body),
                )

    def load_bars(self, symbol, limit=1000):
        from quantpilot.paper.strategy import Bar

        rows = self.db.execute(
            "SELECT body FROM bars WHERE symbol=? ORDER BY start DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
        values = []
        for r in reversed(rows):
            b = json.loads(r[0])
            b["start"] = datetime.fromisoformat(b["start"])
            values.append(Bar(**b))
        return values
