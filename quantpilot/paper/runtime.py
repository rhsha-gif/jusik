"""Bounded execution cycles. AI work is scheduled by a separate process."""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from dataclasses import asdict, replace
from contextlib import nullcontext
from quantpilot.paper.diagnostics import failure, subsystem, open_incident, recover_incident, publish_gates
from types import SimpleNamespace
import math

from quantpilot.paper.calendar import KST
from quantpilot.paper.config import environment_safe
from quantpilot.paper.risk import entry_size, fresh_quote, limit_price, sell_quantity
from quantpilot.paper import latency
from quantpilot.paper.latency import stale_signal
from quantpilot.paper.store import OPEN
from quantpilot.packages.core.kis_paper import safe_failure
from quantpilot.paper.valuation import roll_baselines, record_close


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
        self.budget = None
        self.feed = None
        self._reconciled_at = None
        self._reconcile_ok = False
        self._reconcile_wakeup = None

    def alert(self, code, now, block=True, symbol=None):
        key = "alert:" + now.astimezone(KST).date().isoformat() + ":" + code
        symbols = self.store.get("unverified_symbols", [])
        protection = "\n보호 대기: " + ", ".join(symbols) if symbols else ""
        if symbols:
            key += ":" + ",".join(symbols)
        if block:
            open_incident(self.store, subsystem(code, symbol), code, now)
        if not self.store.get(key):
            self.store.put(key, True)
            self.store.audit("incident", {"code": code}, now)
            self.store.enqueue(
                f"incident:{now.isoformat()}:{code}",
                f"QuantPilot 모의운용 알림: {code}{protection}\n신규 진입은 상태 검증 후 재개합니다.",
                now,
            )

    def io(self, priority):
        return self.budget.context(priority={0: "query", 1: "reconcile", 2: "entry", 3: "minute"}[priority]) if self.budget else nullcontext()

    def cycle(self):
        if self.store.get("incident") and not self.store.get("incidents"):
            open_incident(self.store, "reconciliation", self.store.get("incident"), self.clock())
        held = {p["symbol"] for p in self.store.positions()}
        for scope in tuple(self.store.get("incidents", {})):
            if scope.startswith("protection:") and scope.split(":", 1)[1] not in held:
                recover_incident(self.store, scope, self.clock())
        result = self._cycle()
        now = self.clock()
        reasons = []
        state = result.get("status")
        if state in {"closed", "postclose", "preopen", "stopped"}:
            reasons.append("stopped" if state == "stopped" else "session_closed")
        else:
            if self.store.get("control") != "running":
                reasons.append("manual_pause")
            if self.store.policy.data_mode == "paper_trading" and (not self.store.get("day_base_valid", False) or self.store.get("month_base_valid") is False):
                reasons.append("invalid_baseline")
            if self.store.policy.supervisor_enabled and self.store.get("recovery_required"):
                reasons.append("recovery_required")
            if self.store.policy.hybrid_feed_enabled and not self.store.get("feed_entry_ready", False):
                reasons.append("stale_feed")
            if self.store.get("incidents", {}).get("reconciliation") or self.store.get("reconciliation_complete") is False:
                reasons.append("reconciliation_required")
            loss = self.store.get("intraday_loss_state", {})
            if loss.get("daily_halted") or loss.get("drawdown_halted"):
                reasons.append("loss_limit")
            if result.get("reason"):
                reasons.append(result["reason"])
            if not reasons and state in {"running", "waiting"} and not result.get("signals"):
                statuses = [self.store.get("candidate_status:" + symbol, "warming") for symbol in self.store.get("universe", [])]
                reasons.append("warming" if not statuses or any(v.startswith("warming") or v == "no_fresh_bars" for v in statuses) else "no_signal")
            if self.store.get("entry_cutoff_active"):
                reasons.append("entry_cutoff")
        publish_gates(self.store, reasons, now)
        return result

    def reconcile(self, force=False):
        now = self.clock()
        interval = 10 if self.store.positions() or self.store.orders(True) else 60
        wake = self.store.get("reconcile_wakeup")
        if (self.store.policy.shared_api_budget_enabled and not force and self._reconciled_at
                and 0 <= (now - self._reconciled_at).total_seconds() < interval
                and wake == self._reconcile_wakeup):
            return self._reconcile_ok
        with self.io(1):
            ok = self.gateway.reconcile(now)
        self._reconciled_at, self._reconcile_ok = self.clock(), ok
        self._reconcile_wakeup = wake
        if ok:
            recover_incident(self.store, "reconciliation", self.clock())
            if self.feed:
                self.feed.set_reconciled(now)
        return ok

    def _cycle(self):
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
        self.store.put("heartbeat", now.isoformat())
        retry_after = self.store.get("broker_retry_after")
        if retry_after and now < datetime.fromisoformat(retry_after) and not self.store.positions():
            return {"status": "blocked", "reason": "broker_read_backoff"}
        session = self.calendar.session(now)
        if session is None:
            return {"status": "closed"}
        day = now.astimezone(KST).date().isoformat()
        self.store.put("session_closes", session.closes.isoformat())
        self.store.put("entry_cutoff_active", now >= session.closes - timedelta(minutes=policy.entry_cutoff_minutes))
        if policy.supervisor_enabled and self.store.get("control") == "running" and self.store.get("resume_authorized_day") != day:
            self.store.control("pause", now=now, origin="runtime")
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
        if now >= session.closes and not self.store.positions() and not self.store.orders(True):
            from quantpilot.paper.valuation import close_for_day
            close = close_for_day(self.store, day)
            if close and close.get("valid") and close.get("positions", 0) == 0 and close.get("open_orders", 0) == 0:
                if policy.auto_pause_after_close and self.store.get("control") == "running":
                    self.store.control("pause", now=now, origin="runtime")
                self.store.put("ai_due", {"kind": "postclose", "key": day + ":postclose"})
                return {"status": "postclose", "quarantined": 0}
        begun = False
        cycle_failed = False
        try:
            self.gateway.begin()
            begun = True
            try:
                if retry_after and self.clock() < datetime.fromisoformat(retry_after):
                    reconciled = False
                else:
                    reconciled = self.reconcile()
            except Exception as exc:
                if not self.store.positions():
                    raise
                # An unavailable discovery/reconciliation query cannot skip protection.
                # Gateway ownership, quantity, evidence age and kernel gates still apply.
                reconciled = False
                self._reconcile_ok = False
                self.store.put("reconciliation_complete", False)
                self.store.audit("reconciliation_failed", failure(exc, "reconciliation"), self.clock())
            if not reconciled:
                self.alert(self.store.get("reconciliation_reason") or "broker_order_outcome_unknown", now)
            if self.feed:
                protected = {p["symbol"] for p in self.store.positions()} | {o["symbol"] for o in self.store.orders(True)}
                ready = self.feed.healthy(self.clock(), protected | set(self.store.get("universe", [])[:5]))
                self.store.put("feed_entry_ready", ready)
                if ready:
                    recover_incident(self.store, "feed", self.clock())
                else:
                    self.alert("feed_unavailable", self.clock())
            positions = self.store.positions()
            roll_baselines(self.store, self.calendar, now)
            if now >= session.closes:
                for p in positions:
                    self.store.db.execute(
                        "UPDATE positions SET quarantined=1 WHERE symbol=?",
                        (p["symbol"],),
                    )
                if positions:
                    self.alert("unclosed_positions_quarantined", now, block=False)
                from quantpilot.paper.reporting import snapshot
                # Judge the close valuation at the current clock, not the cycle's start
                # time: reconcile() above stamped last_reconciled_at a few seconds after
                # `now`, and snapshot() treats a negative age as not fresh, which marked
                # every close invalid and blocked the next day's entries.
                record_close(self.store, now,
                             valid=not snapshot(self.store, now=self.clock())["valuation_incomplete"],
                             equity=self.equity())
                self.store.put(
                    "ai_due", {"kind": "postclose", "key": day + ":postclose"}
                )
                if (
                    policy.auto_pause_after_close
                    and self.store.get("control") == "running"
                ):
                    # The next session must be resumed on purpose, never by an
                    # unattended process that simply kept running overnight.
                    self.store.control("pause", now=self.clock(), origin="runtime")
                    self.store.audit("auto_paused_after_close", {"day": day}, now)
                return {"status": "postclose", "quarantined": len(positions)}
            liquidation = now >= session.closes - timedelta(
                minutes=policy.liquidation_minutes
            )
            no_entries = not reconciled or self.store.get(
                "control"
            ) != "running" or now >= session.closes - timedelta(
                minutes=policy.entry_cutoff_minutes
            )
            if policy.data_mode == "paper_trading" and not self.store.get("day_base_valid", False):
                no_entries = True
            # Daily-loss and peak-drawdown halts apply to every strategy generation.
            loss_state = self.store.get("intraday_loss_state", {})
            no_entries = (
                no_entries
                or (loss_state.get("daily_halted", False) and loss_state.get("day") == day)
                or loss_state.get("drawdown_halted", False)
            )
            if policy.strategy_generation == "intraday_v2":
                from quantpilot.paper.intraday.controls import feed_fresh

                no_entries = no_entries or not feed_fresh(self.store, now)
            if policy.hybrid_feed_enabled and not self.store.get("feed_entry_ready", False):
                no_entries = True
            cancelled = False
            # Cancel stale entry/exit limits once, then let reconciliation prove final status.
            for order in self.store.orders(True):
                age = (now - datetime.fromisoformat(order["at"])).total_seconds()
                stale_after = policy.exit_reissue_seconds if order["side"] == "sell" else 60
                if (order["side"] == "buy" and no_entries) or age >= stale_after:
                    try:
                        before_cancel = [(o["id"], o["state"]) for o in self.store.orders(True)]
                        prior_claim = self.store.get("cancel_claim:" + order["id"])
                        with self.io(0):
                            self.gateway.cancel(order, self.clock())
                        cancelled = cancelled or prior_claim != self.store.get("cancel_claim:" + order["id"]) or before_cancel != [(o["id"], o["state"]) for o in self.store.orders(True)]
                    except Exception as exc:
                        self.alert("cancel_reconciliation_required", self.clock())
                        self.store.audit(
                            "cancel_failed",
                            {"order_id": order["id"], **safe_failure(exc, "cancel")},
                            self.clock(),
                        )
            if cancelled:
                try:
                    reconciled = self.reconcile(force=True)
                except Exception as exc:
                    reconciled = False
                    self.store.put("reconciliation_complete", False)
                    self.store.audit("reconciliation_failed", failure(exc, "after_cancel"), self.clock())
                if not reconciled:
                    no_entries = True
                    self.alert("cancel_reconciliation_required", self.clock())
            # Ambiguous rows never resolve themselves; call the operator once they persist.
            if any(
                o["state"] in {"outcome_unknown", "cancel_unknown"}
                and (self.clock() - datetime.fromisoformat(o["at"])).total_seconds()
                >= policy.manual_resolution_after_seconds
                for o in self.store.orders(True)
            ):
                self.alert("manual_resolution_required", self.clock(), block=False)
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
                    with self.io(0):
                        quote = self.market.quotes([p["symbol"]])[p["symbol"]]
                    at = self.clock()
                    fresh_quote(quote, at, policy.quote_ttl_seconds)
                    marks = self.store.get("marks", {})
                    marks[p["symbol"]] = quote.last
                    self.store.put("marks", marks)
                    mark_times = self.store.get("marks_at", {})
                    mark_times[p["symbol"]] = at.isoformat()
                    self.store.put("marks_at", mark_times)
                    recover_incident(self.store, "protection:" + p["symbol"], at)
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
                    latency.observe_exit_condition(self.store, p["symbol"], reason, at)
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
                        symbol=p["symbol"],
                    )
                    self.store.audit(
                        "protection_error",
                        {"symbol": p["symbol"], **failure(exc, "position_protection")},
                    )
            if (
                self.store.get("control") == "flattening"
                and not self.store.positions()
                and not self.store.orders(True)
            ):
                self.store.put("control", "paused")
                self.store.audit("flatten_completed", {}, self.clock())
            from quantpilot.paper.intraday.controls import loss_budget

            budget = loss_budget(self.store, self.clock())
            no_entries = no_entries or not budget["available"]
            if policy.supervisor_enabled and self.store.get("recovery_required"):
                data_ready = (self.store.get("feed_entry_ready") is True if policy.hybrid_feed_enabled
                              else all((self.store.get("data:" + symbol) or {}).get("quality") == "completed_bars_validated"
                                       and (self.store.get("data:" + symbol) or {}).get("last_bar")
                                       and 0 <= (self.clock() - datetime.fromisoformat(self.store.get("data:" + symbol)["last_bar"])).total_seconds() <= 150
                                       for symbol in self.store.get("universe", [])) and bool(self.store.get("universe")))
                if reconciled and self.store.get("day_base_valid") and budget["available"] and data_ready:
                    # A supervised restart into explicit REST mode can retire the
                    # obsolete stream outage only after its normal recovery proof.
                    if (not policy.hybrid_feed_enabled and self.feed is None
                            and self.store.get("incidents", {}).get("feed", {}).get("reason_code") == "feed_unavailable"):
                        recover_incident(self.store, "feed", self.clock())
                    self.store.put("recovery_required", False)
                    self.store.audit("restart_reconciled", {"day": day}, self.clock())
                else:
                    no_entries = True
            if policy.strategy_generation == "intraday_v2":
                from quantpilot.paper.intraday.controls import feed_fresh

                no_entries = no_entries or not feed_fresh(self.store, self.clock())
            if no_entries or self.store.get("incident"):
                return {"status": "protecting", "new_entries": False}
            # Evaluate each completed bar once per symbol, as soon as it is observed.
            # The former clock-minute gate skipped a bar that finalized late in the
            # minute until the next minute. Position protection remains on every cycle.
            bucket = now.replace(second=0, microsecond=0).isoformat()
            fetch_due = self.background_data or self.store.get("fetch_bucket") != bucket
            signals = []
            intraday_histories = {}
            evaluated_any = False
            already_judged = False
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
                    from quantpilot.paper.risk import data_quarantined

                    if data_quarantined(self.store, symbol, self.clock()):
                        self.store.put("candidate_status:" + symbol,
                                       "completed_bar_revised_quarantined")
                        continue
                    fetched_at = self.clock()
                    if not self.background_data and fetch_due:
                        # Foreground reads keep one REST fetch per clock minute per symbol.
                        observations = getattr(self.market, "minute_observations", None)
                        if observations:
                            bars = self.store.observe_bars(observations(symbol, fetched_at), fetched_at)
                        else:
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
                    bar_key = today[-1].start.isoformat()
                    if self.store.get("evaluated_bar:" + symbol) == bar_key:
                        already_judged = True
                        continue  # this completed bar was already judged once
                    bar_end = today[-1].start + timedelta(minutes=1)
                    observed_raw = (self.store.get("data:" + symbol) or {}).get("observed_at")
                    observed_at = datetime.fromisoformat(observed_raw) if observed_raw else fetched_at
                    if policy.strategy_generation == "intraday_v2":
                        from quantpilot.paper.intraday.runtime import signals_for

                        intraday_histories[symbol] = today
                        if bar_end != fetched_at.replace(second=0, microsecond=0):
                            continue  # the next completed bar has not been observed yet
                        found = signals_for(self.store, today, fetched_at, session.opens)
                    else:
                        found = evaluate_strategies(today, fetched_at, session.opens)
                    computed_at = self.clock()
                    signals.extend(
                        replace(s, bar_end=bar_end, observed_at=observed_at,
                                computed_at=computed_at)
                        for s in found
                    )
                    self.store.put("evaluated_bar:" + symbol, bar_key)
                    evaluated_any = True
                    self.store.put(
                        "candidate_status:" + symbol,
                        "ready" if len(today) >= 110 else "warming",
                    )
                except Exception as exc:
                    from quantpilot.paper.store import CompletedBarRevised
                    if isinstance(exc, CompletedBarRevised):
                        from quantpilot.paper.collector import quarantine_revision
                        quarantine_revision(self.store, symbol, exc, self.clock())
                        continue
                    self.store.put("candidate_status:" + symbol, type(exc).__name__)
                    self.store.audit(
                        "candidate_unavailable",
                        {"symbol": symbol, "error": type(exc).__name__},
                        self.clock(),
                    )
            if not self.background_data and fetch_due:
                self.store.put("fetch_bucket", bucket)
            if already_judged and not evaluated_any:
                return {"status": "waiting"}
            scores = {s: 0.0 for s in policy.active_strategies}
            for signal in signals:
                if signal.strategy_id in scores:
                    scores[signal.strategy_id] = max(
                        scores[signal.strategy_id], signal.score
                    )
            ai = self.assessment(self.clock())
            ai_scores = ai.strategy_scores if ai else None
            if ai and policy.strategy_generation == "legacy":
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
                if stale_signal(signal, at):
                    # The bar this signal was judged on is now older than the freshness
                    # gate allows; a slow cycle must not act on it and no order is made.
                    self.store.audit(
                        "signal_stale",
                        {"symbol": signal.symbol, "strategy": signal.strategy_id,
                         "bar_end": signal.bar_end.isoformat(),
                         "age_seconds": (at - signal.bar_end).total_seconds()},
                        at,
                    )
                    continue
                if policy.shared_api_budget_enabled and not self.reconcile(force=True):
                    self.alert("entry_reconciliation_required", self.clock())
                    break
                with self.io(2):
                    quote = self.market.quotes([signal.symbol])[signal.symbol]
                at = self.clock()
                qty = entry_size(
                    self.store, signal, quote, weights.get(signal.strategy_id, 0), at
                )
                if qty:
                    self.place(signal, qty, quote, "buy", signal.reason, at)
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
            cycle_failed = True
            self.alert("execution_reconciliation_required", self.clock())
            self.store.put("reconciliation_complete", False)
            detail = getattr(exc, "detail", None) or safe_failure(exc, "execution_cycle")
            if not isinstance(detail, dict) or "error" not in detail:
                detail = safe_failure(exc, "execution_cycle")
            previous = self.store.get("last_cycle_error", {})
            at = self.clock()
            key = ":".join(str(detail.get(k) or "") for k in ("stage", "error", "broker_code"))
            counts = self.store.get("cycle_error_counts", {})
            counts[key] = counts.get(key, 0) + 1
            self.store.put("cycle_error_counts", counts)
            if (previous.get("key") != key or not previous.get("at") or
                    (at - datetime.fromisoformat(previous["at"])).total_seconds() >= 60):
                self.store.audit("cycle_failed", detail | {"count": counts[key]}, at)
                self.store.put("last_cycle_error", {"key": key, "at": at.isoformat()})
            # Gateway refusals (per-second limit, token throttle) back off like transport
            # failures; retrying every cycle would only keep the limit tripped.
            if detail.get("broker_code") == "EGW00123":
                # KIS reported the token expired ahead of our own schedule.
                invalidate = getattr(self.gateway.client, "invalidate", None)
                if callable(invalidate):
                    invalidate()
                    self.store.audit("token_invalidated_by_broker", {}, at)
            if detail["error"] in {"KisPaperTransportError", "KisPaperGatewayRejected"}:
                failures = min(6, self.store.get("broker_read_failures", 0) + 1)
                self.store.put("broker_read_failures", failures)
                self.store.put("broker_retry_after", (at + timedelta(seconds=min(60, 2 ** failures))).isoformat())
            if self.clock() >= session.closes:
                record_close(self.store, self.clock(), valid=False)
                self.store.put(
                    "ai_due", {"kind": "postclose", "key": day + ":postclose"}
                )
            return {"status": "blocked", "reason": "execution_reconciliation_required"}
        finally:
            if not cycle_failed:
                self.store.put("broker_read_failures", 0)
                self.store.put("broker_retry_after", None)
            if begun:
                try:
                    self.gateway.end()
                    recover_incident(self.store, "runtime", self.clock())
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
                if side == "buy":
                    latency.link_signal(self.store, order_id, signal, now)
                    latency.measure_entry(self.store, signal, price, qty, now)
                else:
                    latency.link_exit(self.store, order_id, signal.symbol,
                                      signal.strategy_id, reason, quote, now)
                    latency.measure_exit(self.store, order_id, signal.symbol, reason, now)
        if reserved:
            with self.io(0 if side == "sell" else 2):
                self.gateway.submit(
                    next(o for o in self.store.orders() if o["id"] == order_id), quote, now
                )
            self.store.put("reconcile_wakeup", self.clock().isoformat())

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
