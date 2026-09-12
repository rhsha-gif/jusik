"""Thin adapter to the existing durable paper submission/reconciliation kernel."""

from __future__ import annotations
from datetime import datetime, timedelta
from decimal import Decimal

from quantpilot.paper.store import OPEN, TERMINAL
from quantpilot.paper.risk import fresh_quote, authorize_order
from quantpilot.paper.order_evidence import daily_identity_matches, daily_quantities_valid
from quantpilot.packages.core.schemas import (
    OrderIntent,
    OrderPlan,
    OrderStatus,
    PortfolioSnapshot,
    PortfolioPosition,
    ProposalExplanation,
)
from quantpilot.packages.core.execution.state_machine import transition_order_plan
from quantpilot.packages.core.execution.paper_submission import (
    DurablePaperSubmissionCoordinator,
)
from quantpilot.packages.core.execution.paper_reconciliation import (
    PaperBrokerReconciler,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore
from quantpilot.packages.db.audit import AuditRecorder


class LedgerAudit:
    def __init__(self, store):
        self.store = store

    def add(self, event):
        self.store.audit(event.action, event.model_dump(mode="json"))
        return event


class KisGateway:
    def __init__(self, store, client, calendar, clock, *, kernel=None):
        self.store = store
        self.client = client
        self.calendar = calendar
        self.clock = clock
        self.kernel = kernel if kernel is not None else PaperStateStore(
            store.path.with_name("broker.sqlite3"),
            data_mode="paper_trading",
            broker_environment="kis_paper",
            account_scope_fingerprint=client.account_scope_fingerprint,
        )
        existing = store.get("account_binding")
        if existing and existing != client.account_scope_fingerprint:
            raise ValueError("experiment_account_mismatch")
        store.put("account_binding", client.account_scope_fingerprint)
        self.reconciler = PaperBrokerReconciler(
            store=self.kernel, client=client, clock=clock
        )
        self.coordinator = None
        self.session = None
        self.balance = None
        self.verified_symbols = set()
        self.entry_reconciled = False

    def begin(self):
        from quantpilot.paper.auth import RefreshingClient

        now = self.clock()
        self.session = self.kernel.start_paper_execution_session(
            started_at=now, lease_expires_at=now + timedelta(minutes=5)
        )
        try:
            self.coordinator = DurablePaperSubmissionCoordinator(
                store=self.kernel,
                session=self.session,
                client=(
                    self.client.current_client()
                    if isinstance(self.client, RefreshingClient)
                    else self.client
                ),
                session_authority=self.calendar,
                clock=self.clock,
            )
            self.coordinator.expire_stale_prepared_dispatches()
        except BaseException:
            self.end()
            raise

    def recover_exclusive_owner(self):
        """CLI holds both account and runtime process locks before calling this."""
        now = self.clock()
        for session in self.kernel.list_paper_execution_sessions():
            if session.status == "active" and session.lease_expires_at > now:
                self.kernel.close_paper_execution_session(
                    session,
                    closed_at=max(now, session.updated_at + timedelta(microseconds=1)),
                )
                self.store.audit(
                    "orphan_session_closed", {"session_id": session.session_id}, now
                )

    def end(self):
        if self.session:
            self.kernel.close_paper_execution_session(
                self.session,
                closed_at=max(
                    self.clock(), self.session.updated_at + timedelta(microseconds=1)
                ),
            )
            self.session = None

    def close(self):
        self.kernel.close()

    def reconcile(self, now):
        self.verified_symbols = set()
        self.entry_reconciled = False
        self.store.put("reconciliation_complete", False)
        self.store.put("reconciliation_attempt_at", now.isoformat())
        result = self.reconciler.reconcile_unresolved()
        diagnostics = list(result.diagnostics)
        if diagnostics != self.store.get("reconciliation_diagnostics"):
            self.store.audit("reconciliation_diagnostics", {"orders": diagnostics}, now)
        self.store.put("reconciliation_diagnostics", diagnostics)
        self.balance = result.broker_balance
        before_positions = {p["symbol"]: p["quantity"] for p in self.store.positions()}
        for order in self.store.orders(True):
            dispatch = self.kernel.load_paper_order_dispatch(order["id"])
            if dispatch is None:
                # No durable POST intent exists. It is safe to expire a local pre-intent.
                if order["state"] == "prepared":
                    self.store.update_order(
                        order["id"], "expired_pre_dispatch", 0, 0, now
                    )
                else:
                    raise ValueError("dispatch_missing")
                continue
            qty = int(dispatch.cumulative_filled_quantity)
            amount = sum(f.notional for f in dispatch.fill_evidence)
            state = dispatch.status
            if state == "dispatch_claimed":
                state = "outcome_unknown"
            if self.store.get("cancel_claim:" + order["id"]) and state not in TERMINAL:
                state = "cancel_unknown"
            self.store.update_order(
                order["id"], state, qty, amount, now, dispatch.broker_order_reference
            )
        local = {p["symbol"]: p["quantity"] for p in self.store.positions()}
        self.store.verify_cash()
        # The reconciler's balance predates its daily-order query. When that query
        # just applied a fill, the holding may have moved after the balance snapshot,
        # so refresh it; otherwise reuse the snapshot and save the request budget.
        if local != before_positions:
            self.balance = self.client.get_balance()
        remote = {
            p.symbol: p.holding_quantity
            for p in self.balance.positions
            if p.holding_quantity
        }
        self.verified_symbols = {
            symbol
            for symbol, quantity in local.items()
            if remote.get(symbol) == quantity
        }
        self.store.put("unverified_symbols", sorted((set(local) | set(remote)) - self.verified_symbols))
        from quantpilot.paper.calendar import KST

        business_date = now.astimezone(KST).date()
        rows = self.client.get_daily_orders_and_fills(
            business_date, business_date, exchange="KRX", as_of_date=business_date,
        ).rows
        dispatches = self.kernel.list_paper_order_dispatches()
        # Terminal, conserved rows need no working-order attribution. Invalid or
        # ambiguous daily evidence blocks entries rather than disappearing as zero.
        invalid_rows = any(not daily_quantities_valid(row) for row in rows)
        identities = [(r.order_date, r.order_branch_number, r.order_number) for r in rows]
        duplicate_rows = len(identities) != len(set(identities))
        unmatched_rows = [
            row
            for row in rows
            if row.remaining_quantity > 0
            if not any(
                daily_identity_matches(d, row, business_date)
                for d in dispatches
            )
        ]
        unmanaged = bool(unmatched_rows) or invalid_rows or duplicate_rows
        known_state_changed = bool(unmatched_rows) and all(
            any(
                d.broker_order_reference == row.order_number
                and d.broker_order_branch_number == row.order_branch_number
                and d.symbol == row.symbol
                and d.side == row.side
                and int(d.quantity) == row.order_quantity
                for d in dispatches
            )
            for row in unmatched_rows
        )
        self.store.put(
            "reconciliation_reason",
            (
                (
                    "daily_order_evidence_invalid"
                    if invalid_rows or duplicate_rows
                    else "known_order_state_changed"
                    if known_state_changed
                    else "external_working_order"
                )
                if unmanaged
                else (
                    "account_exposure_unattributed"
                    if local != remote
                    else "broker_order_outcome_unknown"
                )
            ),
        )
        # A verified balance can still protect unrelated attributed positions while
        # an individual dispatch is uncertain. No new entry is authorized in that state.
        self.entry_reconciled = (
            not bool(result.blocked_order_plan_ids)
            and all(d["matched_rows"] == 1 for d in diagnostics)
            and local == remote
            and not unmanaged
        )
        self.store.put("reconciliation_complete", self.entry_reconciled)
        if self.entry_reconciled:
            self.store.put("reconciliation_reason", None)
            self.store.put("last_reconciled_at", now.isoformat())
        # Balance marks are valuation observations, never executable quotes.
        marks = self.store.get("marks", {})
        times = self.store.get("marks_at", {})
        sources = self.store.get("mark_sources", {})
        for position in self.balance.positions:
            if position.symbol in self.verified_symbols and position.current_price > 0:
                marks[position.symbol] = float(position.current_price)
                times[position.symbol] = now.isoformat()
                sources[position.symbol] = "broker_balance"
        self.store.put("marks", marks)
        self.store.put("marks_at", times)
        self.store.put("mark_sources", sources)
        return self.entry_reconciled

    def protection_allowed(self, symbol):
        return symbol in self.verified_symbols

    def submit(self, order, quote, now):
        if self.coordinator is None:
            raise ValueError("execution_lease_required")
        policy = self.store.policy
        fresh_quote(quote, now, policy.quote_ttl_seconds)
        authorize_order(self.store, order, quote, now, self.calendar.session(now))
        # Latest balance and experiment sizing are checked immediately before this adapter.
        if self.balance is None:
            raise ValueError("balance_not_reconciled")
        if order["side"] == "buy" and not self.entry_reconciled:
            raise ValueError("entry_reconciliation_required")
        if order["side"] == "sell" and not self.protection_allowed(order["symbol"]):
            raise ValueError("position_reconciliation_required")
        if order["policy_version"] != policy.version:
            raise ValueError("policy_version_conflict")
        if order["side"] == "buy" and self.store.get("control") != "running":
            raise ValueError("new_entries_paused")
        if order["side"] == "sell":
            remote = next(
                (p for p in self.balance.positions if p.symbol == order["symbol"]), None
            )
            if remote is None or remote.orderable_quantity < order["quantity"]:
                raise ValueError("broker_sell_quantity_unavailable")
        marks = self.store.get("marks", {})
        equity = self.store.get("cash") + sum(
            p["quantity"] * marks.get(p["symbol"], p["basis"] / p["quantity"])
            for p in self.store.positions()
        )
        snapshot = PortfolioSnapshot(
            user_id="paper-experiment",
            cash=self.store.get("cash"),
            equity=equity,
            positions=[
                PortfolioPosition(
                    symbol=p["symbol"],
                    quantity=p["quantity"],
                    orderable_quantity=next(
                        (
                            b.orderable_quantity
                            for b in self.balance.positions
                            if b.symbol == p["symbol"]
                        ),
                        0,
                    ),
                    market_price=marks.get(p["symbol"], p["basis"] / p["quantity"]),
                    sector="experiment",
                )
                for p in self.store.positions()
            ],
            captured_at=now,
            source=(
                "paper_experiment_marked"
                if all(p["symbol"] in marks for p in self.store.positions())
                else "paper_experiment_partial_cost_basis"
            ),
            daily_loss_ratio=equity / self.store.get("day_base", equity) - 1,
            monthly_loss_ratio=equity / self.store.get("month_base", equity) - 1,
        )
        notional = order["quantity"] * order["price"]
        weight = notional / equity
        explanation = ProposalExplanation(
            symbol=order["symbol"],
            action=order["side"],
            quantity=order["quantity"],
            target_weight_delta=weight,
            reference_price=quote.last,
            estimated_cash_impact=notional,
            strategy_id=order["strategy"],
            strategy_version=order["version"],
            signal_reason=order["reason"],
            current_weight=0,
            target_weight=weight,
            weight_delta=weight,
            quote_price=quote.last,
            quote_age_seconds=(now - quote.as_of).total_seconds(),
            limit_price=order["price"],
            estimated_notional=notional,
            stop_price_hint=order["stop"],
            take_profit_hint=order["target"],
            idempotency_key=order["id"],
            policy_version=policy.version,
        )
        plan = OrderPlan(
            order_plan_id=order["id"],
            policy_id="intraday-paper",
            policy_version=policy.version,
            idempotency_key=order["id"],
            intent=OrderIntent(
                symbol=order["symbol"],
                side=order["side"],
                quantity=order["quantity"],
                limit_price=order["price"],
                notional=notional,
                target_weight=weight,
                reason=order["reason"],
                quote_time=quote.as_of,
            ),
            purpose="protective_exit" if order["side"] == "sell" else "rebalance",
            explanation=explanation,
            risk_check_id="risk:" + order["id"],
            risk_check_expires_at=now + timedelta(seconds=policy.quote_ttl_seconds),
            expires_at=now + timedelta(seconds=60),
        )
        audit = AuditRecorder(LedgerAudit(self.store))
        for state in (
            OrderStatus.risk_checked,
            OrderStatus.proposed,
            OrderStatus.user_approved,
            OrderStatus.submitted,
        ):
            # Session-scoped operator authority, never an invented per-order human approval.
            transition_order_plan(
                order_plan=plan,
                new_status=state,
                audit=audit,
                user_id="paper-experiment",
                source="explicit_paper_profile",
                action=(
                    "operator_order_authorized"
                    if state == OrderStatus.user_approved
                    else None
                ),
            )
        reserve = max(0.0, snapshot.cash - notional * (1 + policy.fee_bps / 10000))
        power = None
        if order["side"] == "buy":
            power = self.client.get_buying_power(
                order["symbol"], Decimal(str(order["price"])), exchange="KRX"
            )
            other_reserved = sum(
                (o["quantity"] - o["filled"])
                * o["price"]
                * (1 + policy.fee_bps / 10000)
                for o in self.store.orders(True)
                if o["side"] == "buy" and o["id"] != order["id"]
            )
            broker_cash = float(
                min(power.orderable_cash, power.no_receivable_buy_amount)
            )
            outside = self.store.get("outside_cash_reserve")
            if outside is None:
                outside = max(0.0, broker_cash - self.store.get("cash"))
                self.store.put("outside_cash_reserve", outside)
            if notional * (1 + policy.fee_bps / 10000) > min(
                broker_cash - outside, snapshot.cash - other_reserved
            ):
                raise ValueError("broker_experiment_slice_exhausted")
            self.store.audit(
                "broker_slice_checked",
                {
                    "outside_cash_reserve": outside,
                    "experiment_available": max(0.0, broker_cash - outside),
                },
                now,
            )
        self.coordinator.prepare_order(
            plan,
            run_id="intraday:" + now.isoformat(),
            user_id="paper-experiment",
            snapshot=snapshot,
            quote=quote,
            entry_atr14=order.get("entry_atr14"),
            quote_max_age_seconds=policy.quote_ttl_seconds,
            snapshot_max_age_seconds=policy.quote_ttl_seconds,
            minimum_cash_reserve=reserve,
            buying_power=power,
        )
        self.store.update_order(order["id"], "submitted", 0, 0, now)
        self.coordinator.submit_prepared_order(plan)

    def cancel(self, order, now):
        if self.store.get("cancel_claim:" + order["id"]):
            return
        dispatch = self.kernel.load_paper_order_dispatch(order["id"])
        if dispatch is None:
            return
        if dispatch.status not in {"accepted", "partially_filled"}:
            return
        from quantpilot.paper.calendar import KST

        business_date = now.astimezone(KST).date()
        rows = self.client.get_daily_orders_and_fills(
            business_date, business_date, exchange="KRX", as_of_date=business_date,
        ).rows
        matches = [
            r
            for r in rows
            if r.remaining_quantity > 0
            and daily_identity_matches(dispatch, r, business_date)
        ]
        if len(matches) != 1 or not dispatch.broker_forwarding_order_org_number:
            self.store.audit(
                "cancel_deferred_to_reconciliation", {
                    "order_id": order["id"],
                    "reason": "daily_evidence_unmatched" if len(matches) != 1
                    else "missing_forwarding_id",
                }, now
            )
            return
        with self.store.transaction():
            self.store.put("cancel_claim:" + order["id"], True)
            self.store.audit("cancel_claimed", {"order_id": order["id"]}, now)
            self.store.update_order(
                order["id"], "cancel_unknown", order["filled"], order["amount"], now
            )
        # A crash/timeout after the claim is query-only, never an automatic re-POST.
        acknowledgement = self.client.cancel_paper_remaining_order(
            forwarding_org_number=dispatch.broker_forwarding_order_org_number,
            original_order_number=dispatch.broker_order_reference,
        )
        self.store.audit("cancel_acknowledged", {
            "order_id": order["id"], "message_code": acknowledgement.message_code,
            "final_state_confirmed": False,
        }, now)


class FixtureGateway:
    """Explicit offline fixture transport. Never presented as a KIS fill."""

    def __init__(self, store):
        self.store = store

    def begin(self):
        pass

    def end(self):
        pass

    def close(self):
        pass

    def reconcile(self, now):
        return True

    def protection_allowed(self, symbol):
        return True

    def submit(self, order, quote, now):
        self.store.update_order(
            order["id"],
            "filled",
            order["quantity"],
            order["quantity"] * order["price"],
            now,
            "fixture",
        )

    def cancel(self, order, now):
        self.store.update_order(
            order["id"], "cancelled", order["filled"], order["amount"], now
        )
