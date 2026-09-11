"""Bounded execution cycles. AI work is scheduled by a separate process."""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from dataclasses import asdict
from types import SimpleNamespace
import math

from quantpilot.paper.calendar import KST
from quantpilot.paper.config import environment_safe
from quantpilot.paper.risk import entry_size, fresh_quote, limit_price, sell_quantity
from quantpilot.paper.store import OPEN


class Runtime:
    def __init__(
        self,
        store,
        market,
        gateway,
        calendar,
        clock=None,
        environment=None,
        background_data=False,
    ):
        self.store = store
        self.market = market
        self.gateway = gateway
        self.calendar = calendar
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.environment = environment or {}
        self.background_data = background_data

    def alert(self, code, now, block=True):
        key = "alert:" + now.astimezone(KST).date().isoformat() + ":" + code
        symbols = self.store.get("unverified_symbols", [])
        protection = "\n보호 대기: " + ", ".join(symbols) if symbols else ""
        if symbols:
            key += ":" + ",".join(symbols)
        if block:
            self.store.put("incident", code)
        if not self.store.get(key):
            self.store.put(key, True)
            self.store.audit("incident", {"code": code}, now)
            self.store.enqueue(
                f"incident:{now.isoformat()}:{code}",
                f"QuantPilot 모의운용 알림: {code}{protection}\n신규 진입은 상태 검증 후 재개합니다.",
                now,
            )

    def cycle(self):
        from quantpilot.paper.strategy import (
            evaluate_strategies,
            allocate_weights,
            select_signals,
        )

        now = self.clock()
        policy = self.store.policy
        if not environment_safe(self.environment):
            self.alert("unsafe_environment", now)
            return {"status": "blocked", "reason": "unsafe_environment"}
        if (
            policy.data_mode == "paper_trading"
            and self.environment.get(
                "KIS_PAPER_ORDER_SUBMISSION_ENABLED", "false"
            ).lower()
            != "true"
        ):
            return {"status": "blocked", "reason": "paper_submission_disabled"}
        if self.store.get("control") == "stopped":
            return {"status": "stopped"}
        session = self.calendar.session(now)
        if session is None:
            return {"status": "closed"}
        day = now.astimezone(KST).date().isoformat()
        self.store.put("session_closes", session.closes.isoformat())
        self.store.put("heartbeat", now.isoformat())
        if now < session.opens:
            self.store.put(
                "evidence",
                {
                    "observed_at": now.isoformat(),
                    "symbols": self.store.get("universe", []),
                    "strategies": list(policy.active_strategies),
                    "positions": self.store.positions(),
                    "phase": "preopen",
                    "market_data_status": "not_yet_observed",
                },
            )
            self.store.put("ai_due", {"kind": "preopen", "key": day + ":preopen"})
            return {"status": "preopen"}
        hourly = max(0, int((now - session.opens).total_seconds() // 3600))
        if (
            policy.strategy_generation == "legacy"
            and hourly >= 1
            and now < session.closes
        ):
            self.store.put(
                "ai_due", {"kind": "hourly", "key": f"{day}:hourly:{hourly}"}
            )
        begun = False
        try:
            self.gateway.begin()
            begun = True
            reconciled = self.gateway.reconcile(self.clock())
            if not reconciled:
                self.alert(
                    self.store.get(
                        "reconciliation_reason", "broker_order_outcome_unknown"
                    ),
                    now,
                )
            elif self.store.get("incident"):
                self.store.audit("recovered", {"code": self.store.get("incident")}, now)
                self.store.put("incident", None)
            positions = self.store.positions()
            if self.store.get("day") != day:
                self.store.put("day", day)
                self.store.put(
                    "day_base",
                    self.store.get(
                        "last_close_equity", self.store.get("initial_capital")
                    ),
                )
            if self.store.get("month") != day[:7]:
                self.store.put("month", day[:7])
                self.store.put(
                    "month_base",
                    self.store.get(
                        "last_close_equity", self.store.get("initial_capital")
                    ),
                )
            if now >= session.closes:
                for p in positions:
                    self.store.db.execute(
                        "UPDATE positions SET quarantined=1 WHERE symbol=?",
                        (p["symbol"],),
                    )
                if positions:
                    self.alert("unclosed_positions_quarantined", now, block=False)
                self.store.put("last_close_equity", self.equity())
                self.store.put(
                    "ai_due", {"kind": "postclose", "key": day + ":postclose"}
                )
                return {"status": "postclose", "quarantined": len(positions)}
            liquidation = now >= session.closes - timedelta(
                minutes=policy.liquidation_minutes
            )
            no_entries = self.store.get(
                "control"
            ) != "running" or now >= session.closes - timedelta(
                minutes=policy.entry_cutoff_minutes
            )
            if policy.strategy_generation == "intraday_v2":
                from quantpilot.paper.intraday.controls import feed_fresh

                loss_state = self.store.get("intraday_loss_state", {})
                no_entries = (
                    no_entries
                    or loss_state.get("daily_halted", False)
                    and loss_state.get("day") == day
                    or loss_state.get("drawdown_halted", False)
                    or not feed_fresh(self.store, now)
                )
            # Cancel stale entry/exit limits once, then let reconciliation prove final status.
            for order in self.store.orders(True):
                age = (now - datetime.fromisoformat(order["at"])).total_seconds()
                if (order["side"] == "buy" and no_entries) or age >= 60:
                    try:
                        self.gateway.cancel(order, self.clock())
                    except Exception as exc:
                        self.alert("cancel_reconciliation_required", self.clock())
                        self.store.audit(
                            "cancel_failed",
                            {"order_id": order["id"], "error": type(exc).__name__},
                            self.clock(),
                        )
            if not self.gateway.reconcile(self.clock()):
                self.alert(
                    self.store.get(
                        "reconciliation_reason", "broker_order_outcome_unknown"
                    ),
                    self.clock(),
                )
            # Protect attributed positions before spending requests on discovery.
            for p in self.store.positions():
                if not self.gateway.protection_allowed(p["symbol"]):
                    self.store.audit(
                        "position_awaiting_reconciliation",
                        {"symbol": p["symbol"]},
                        self.clock(),
                    )
                    continue
                try:
                    quote = self.market.quotes([p["symbol"]])[p["symbol"]]
                    at = self.clock()
                    fresh_quote(quote, at, policy.quote_ttl_seconds)
                    marks = self.store.get("marks", {})
                    marks[p["symbol"]] = quote.last
                    self.store.put("marks", marks)
                    mark_times = self.store.get("marks_at", {})
                    mark_times[p["symbol"]] = at.isoformat()
                    self.store.put("marks_at", mark_times)
                    extra_exit = None
                    if policy.strategy_generation == "intraday_v2" and p[
                        "version"
                    ].startswith("intraday2:"):
                        from quantpilot.paper.intraday.strategy import (
                            get_spec,
                            protective_exit,
                        )

                        history = [
                            b
                            for b in self.store.load_bars(p["symbol"])
                            if session.opens <= b.start
                            and b.start + timedelta(minutes=1) <= at
                        ]
                        opened = datetime.fromisoformat(p["opened"])
                        try:
                            stop, extra_exit = protective_exit(
                                dict(p, opened=opened),
                                history,
                                at,
                                get_spec(p["version"]),
                            )
                        except (ValueError, StopIteration):
                            # Evidence invalidation or unusable bars cannot disable liquidation.
                            stop, extra_exit = (
                                p["stop"],
                                "strategy_protection_unavailable",
                            )
                            self.store.audit(
                                "intraday_protection_fallback",
                                {"symbol": p["symbol"]},
                                at,
                            )
                        if stop > p["stop"]:
                            self.store.db.execute(
                                "UPDATE positions SET stop=? WHERE symbol=?",
                                (stop, p["symbol"]),
                            )
                            self.store.audit(
                                "intraday_trailing_stop",
                                {"symbol": p["symbol"], "stop": stop},
                                at,
                            )
                            p["stop"] = stop
                    reason = (
                        "flatten"
                        if self.store.get("control") == "flattening"
                        else (
                            "close"
                            if liquidation
                            else (
                                "quarantine"
                                if p["quarantined"]
                                else (
                                    "stop"
                                    if quote.bid <= p["stop"]
                                    else (
                                        "target"
                                        if quote.bid >= p["target"]
                                        else extra_exit
                                    )
                                )
                            )
                        )
                    )
                    if reason:
                        qty = sell_quantity(self.store, p["symbol"], quote, at)
                        if qty:
                            self.place(
                                SimpleNamespace(
                                    strategy_id=p["strategy"],
                                    symbol=p["symbol"],
                                    price=quote.last,
                                    stop=p["stop"],
                                    target=p["target"],
                                    score=1.0,
                                    version=p["version"],
                                ),
                                qty,
                                quote,
                                "sell",
                                reason,
                                at,
                            )
                except Exception as exc:
                    self.alert(
                        "position_protection_unavailable",
                        self.clock(),
                        block=not bool(p["quarantined"]),
                    )
                    self.store.audit(
                        "protection_error",
                        {"symbol": p["symbol"], "error": type(exc).__name__},
                    )
            if (
                self.store.get("control") == "flattening"
                and not self.store.positions()
                and not self.store.orders(True)
            ):
                self.store.put("control", "paused")
                self.store.audit("flatten_completed", {}, self.clock())
            if policy.strategy_generation == "intraday_v2":
                from quantpilot.paper.intraday.controls import loss_budget, feed_fresh

                budget = loss_budget(self.store, self.clock())
                no_entries = (
                    no_entries
                    or not budget["available"]
                    or not feed_fresh(self.store, self.clock())
                )
            if no_entries or self.store.get("incident"):
                return {"status": "protecting", "new_entries": False}
            # Evaluate each completed minute once. Position protection remains on every cycle.
            bucket = now.replace(second=0, microsecond=0).isoformat()
            if self.store.get("signal_bucket") == bucket:
                return {"status": "waiting"}
            signals = []
            intraday_histories = {}
            intraday_waiting = False
            selected_at = self.store.get("universe_at")
            if not self.background_data and (
                selected_at is None
                or (now - datetime.fromisoformat(selected_at)).total_seconds() >= 300
            ):
                symbols, source = self.market.candidates(now, policy.max_candidates)
                self.store.put("universe", symbols)
                self.store.put("universe_at", now.isoformat())
                self.store.put("universe_source", source)
            for symbol in self.store.get("universe", []):
                try:
                    fetched_at = self.clock()
                    if not self.background_data:
                        bars = self.market.minutes(symbol, fetched_at)
                        self.store.save_bars(bars)
                        self.store.put(
                            "data:" + symbol,
                            {
                                "provider": "kis_paper",
                                "observed_at": fetched_at.isoformat(),
                                "quality": "completed_bars_validated",
                                "last_bar": (
                                    bars[-1].start.isoformat() if bars else None
                                ),
                            },
                        )
                    history = self.store.load_bars(symbol)
                    today = [
                        b
                        for b in history
                        if b.start.astimezone(KST).date() == now.astimezone(KST).date()
                    ]
                    # Recover from a historical gap using only the latest contiguous suffix.
                    for index in range(len(today) - 1, 0, -1):
                        if today[index].start - today[index - 1].start != timedelta(
                            minutes=1
                        ):
                            today = today[index:]
                            self.store.put(
                                "candidate_status:" + symbol, "warming_after_gap"
                            )
                            break
                    if (
                        not today
                        or (fetched_at - today[-1].start).total_seconds() > 150
                    ):
                        self.store.put("candidate_status:" + symbol, "no_fresh_bars")
                        continue
                    if policy.strategy_generation == "intraday_v2":
                        from quantpilot.paper.intraday.runtime import signals_for

                        intraday_histories[symbol] = today
                        if today[-1].start + timedelta(minutes=1) != fetched_at.replace(
                            second=0, microsecond=0
                        ):
                            intraday_waiting = True
                            continue
                        signals.extend(
                            signals_for(self.store, today, fetched_at, session.opens)
                        )
                    else:
                        signals.extend(
                            evaluate_strategies(today, fetched_at, session.opens)
                        )
                    self.store.put(
                        "candidate_status:" + symbol,
                        "ready" if len(today) >= 110 else "warming",
                    )
                except Exception as exc:
                    self.store.put("candidate_status:" + symbol, type(exc).__name__)
                    self.store.audit(
                        "candidate_unavailable",
                        {"symbol": symbol, "error": type(exc).__name__},
                        self.clock(),
                    )
            scores = {s: 0.0 for s in policy.active_strategies}
            for signal in signals:
                if signal.strategy_id in scores:
                    scores[signal.strategy_id] = max(
                        scores[signal.strategy_id], signal.score
                    )
            ai = self.assessment(self.clock())
            ai_scores = ai.strategy_scores if ai else None
            if ai and policy.strategy_generation == "legacy":
                from dataclasses import replace

                signals = [
                    replace(
                        s,
                        score=0.8 * s.score
                        + 0.2 * ai.candidate_scores.get(s.symbol, s.score),
                    )
                    for s in signals
                ]
            weights = allocate_weights(
                scores, self.performance(), ai_scores, cap=policy.strategy_cap
            )
            if policy.strategy_generation == "intraday_v2":
                from quantpilot.paper.intraday.runtime import allocate, schedule
                from quantpilot.paper.intraday.strategy import rank

                weights = allocate(signals, ai, policy.strategy_cap)
                select_signals = rank
                schedule(self.store, intraday_histories, self.clock(), session)
            if policy.research_enabled:
                from quantpilot.paper.research import candidates
                from quantpilot.paper.strategy import Signal

                cache = self.store.get("trial_signals", {})
                stamp = datetime.fromisoformat(cache["observed_at"]) if cache else None
                trials = [c for c in candidates(self.store) if c.stage == "paper_trial"]
                if (
                    stamp
                    and stamp.tzinfo
                    and 0 <= (self.clock() - stamp).total_seconds() < 75
                    and len(trials) == 1
                ):
                    candidate = trials[0]
                    trial = [
                        Signal(**s)
                        for s in cache["signals"]
                        if s["strategy_id"] == candidate.strategy_id
                        and s["version"] == candidate.version
                    ]
                    if trial:
                        weights = {s: w * 0.9 for s, w in weights.items()}
                        weights[candidate.strategy_id] = min(
                            0.1, candidate.allocation_cap
                        )
                        signals.extend(trial)
            self.store.put("weights", weights)
            self.store.put("assessment_mode", "ai_assisted" if ai else "rules_only")
            occupied = {p["symbol"] for p in self.store.positions()}
            for signal in select_signals(signals, weights, occupied):
                at = self.clock()
                if at >= session.closes - timedelta(
                    minutes=policy.entry_cutoff_minutes
                ):
                    break
                if signal.strategy_id not in weights:
                    continue
                quote = self.market.quotes([signal.symbol])[signal.symbol]
                at = self.clock()
                qty = entry_size(
                    self.store, signal, quote, weights.get(signal.strategy_id, 0), at
                )
                if qty:
                    self.place(signal, qty, quote, "buy", signal.reason, at)
            if not intraday_waiting:
                self.store.put("signal_bucket", bucket)
            hourly = max(0, int((now - session.opens).total_seconds() // 3600))
            if policy.strategy_generation == "legacy" and hourly >= 1:
                self.store.put(
                    "ai_due", {"kind": "hourly", "key": f"{day}:hourly:{hourly}"}
                )
            if not self.background_data:
                self.store.put(
                    "evidence",
                    {
                        "observed_at": now.isoformat(),
                        "symbols": self.store.get("universe", []),
                        "strategies": list(policy.active_strategies),
                        "market_scores": scores,
                        "weights": weights,
                        "positions": self.store.positions(),
                    },
                )
            return {
                "status": "running",
                "signals": len(signals),
                "weights": weights,
                "data_mode": policy.data_mode,
            }
        except Exception as exc:
            self.alert("execution_reconciliation_required", self.clock())
            self.store.audit(
                "cycle_failed", {"error": type(exc).__name__}, self.clock()
            )
            if self.clock() >= session.closes:
                self.store.put(
                    "ai_due", {"kind": "postclose", "key": day + ":postclose"}
                )
            return {"status": "blocked", "reason": "execution_reconciliation_required"}
        finally:
            if begun:
                try:
                    self.gateway.end()
                except Exception:
                    self.alert("execution_lease_close_failed", self.clock())

    def place(self, signal, qty, quote, side, reason, now):
        policy = self.store.policy
        price = limit_price(quote, side)
        order_id = sha256(
            f"{signal.strategy_id}|{signal.version}|{signal.symbol}|{side}|{now.replace(second=0,microsecond=0).isoformat()}".encode()
        ).hexdigest()
        from quantpilot.paper.risk import authorize_order

        with self.store.transaction():
            # Serialize the final size check with reservation and operator control changes.
            if side == "buy":
                qty = min(
                    qty,
                    entry_size(
                        self.store,
                        signal,
                        quote,
                        self.store.get("weights", {}).get(signal.strategy_id, 0),
                        now,
                    ),
                )
                if not qty:
                    return
            reserved = self.store.reserve(
                order_id=order_id,
                signal=signal,
                quantity=qty,
                price=price,
                side=side,
                now=now,
                policy_version=policy.version,
                reason=reason,
            )
            if reserved:
                order = next(o for o in self.store.orders() if o["id"] == order_id)
                authorize_order(
                    self.store, order, quote, now, self.calendar.session(now)
                )
        if reserved:
            self.gateway.submit(
                next(o for o in self.store.orders() if o["id"] == order_id), quote, now
            )

    def assessment(self, now):
        raw = self.store.get("assessment")
        if not raw or not self.store.policy.ai_enabled:
            return None
        try:
            from quantpilot.paper.intelligence import Assessment
            from quantpilot.paper.store import encode

            value = Assessment.model_validate_json(encode(raw))
            if (
                self.store.policy.strategy_generation == "intraday_v2"
                and (now - value.observed_at).total_seconds() >= 1800
            ):
                return None
            return value if value.usable(now) else None
        except Exception:
            return None

    def performance(self):
        result = {}
        strategy_ids = set(self.store.policy.active_strategies)
        strategy_ids.update(
            row[0]
            for row in self.store.db.execute("SELECT DISTINCT strategy FROM trades")
        )
        for s in sorted(strategy_ids):
            values = [
                r[0]
                for r in self.store.db.execute(
                    "SELECT SUM(t.adjusted_pnl) FROM trades t JOIN orders o ON o.id=substr(t.id,1,instr(t.id,':')-1) WHERE t.strategy=? AND o.state IN ('filled','cancelled') GROUP BY o.id ORDER BY MIN(t.at)",
                    (s,),
                )
            ]
            r_budget = self.store.get("initial_capital") * self.store.policy.trade_risk
            total = peak = drawdown = 0.0
            for v in values:
                total += v
                peak = max(peak, total)
                drawdown = max(drawdown, peak - total)
            result[s] = {
                "trades": len(values),
                "mean_r": sum(values) / max(1, len(values)) / r_budget,
                "max_drawdown": drawdown / self.store.get("initial_capital"),
            }
        return result

    def equity(self):
        if self.store.policy.strategy_generation == "intraday_v2":
            from quantpilot.paper.intraday.controls import loss_budget

            return loss_budget(self.store, self.clock())["equity"]
        marks = self.store.get("marks", {})
        return self.store.get("cash") + sum(
            p["quantity"] * marks.get(p["symbol"], p["basis"] / p["quantity"])
            for p in self.store.positions()
        )
