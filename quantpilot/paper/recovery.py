"""Query-only broker recovery, previewed in memory and applied under owner locks.

The transport cannot submit or cancel orders, even when the caller is armed.
Only cumulative broker evidence enters the existing durable reconciliation path.
"""

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
from uuid import uuid4

from quantpilot.paper.config import environment_safe
from quantpilot.paper.store import Store
from quantpilot.paper.calendar import KST
from quantpilot.paper.valuation import roll_baselines, record_close
from quantpilot.paper.broker import KisGateway
from quantpilot.packages.db.paper_status_reader import PaperStatusReader
from quantpilot.packages.core.kis_paper import (
    KIS_PAPER_BASE_URL, KIS_TOKEN_ENDPOINT, KIS_BALANCE_ENDPOINT,
    KIS_DAILY_ORDERS_ENDPOINT, KisPaperClient, KisPaperConfigurationError,
    StrictUrllibKisPaperTransport,
)


class RecoveryTransport(StrictUrllibKisPaperTransport):
    def request_json(self, method, url, **kwargs):
        allowed = {("POST", KIS_PAPER_BASE_URL + KIS_TOKEN_ENDPOINT)} | {
            ("GET", KIS_PAPER_BASE_URL + path)
            for path in (KIS_BALANCE_ENDPOINT, KIS_DAILY_ORDERS_ENDPOINT)
        }
        if (method, url) not in allowed:
            raise KisPaperConfigurationError("recovery_endpoint_blocked")
        return super().request_json(method, url, **kwargs)


def build_client(env, clock):
    if not environment_safe(env):
        raise ValueError("unsafe_environment")
    from quantpilot.jobs.check_kis_paper_connection import connection_config
    from quantpilot.paper.auth import RefreshingClient
    from quantpilot.paper.data import LimitedTransport

    transport = LimitedTransport(RecoveryTransport())
    return RefreshingClient(connection_config(env),
                            lambda cfg: KisPaperClient(cfg, transport=transport), clock)


def read_connection(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("recovery_ledger_missing")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def copy_memory(path):
    source = read_connection(path)
    target = sqlite3.connect(":memory:", isolation_level=None)
    target.row_factory = sqlite3.Row
    try:
        source.backup(target)
    except BaseException:
        target.close()
        raise
    finally:
        source.close()
    return target


class PreviewKernel:
    """Validate the durable projections, then simulate updates only in memory."""
    def __init__(self, db):
        self.provenance = PaperStatusReader._read_provenance(db)
        sessions = PaperStatusReader._read_sessions(db, provenance=self.provenance)
        rows = PaperStatusReader._read_dispatches(db, provenance=self.provenance, sessions=sessions)
        self.rows = {d.order_plan_id: d for d in rows}

    def list_paper_order_dispatches(self):
        return list(self.rows.values())

    def list_unresolved_paper_order_dispatches(self):
        return [d for d in self.rows.values() if d.reconciliation_status != "reconciled"]

    def load_paper_order_dispatch(self, key):
        return self.rows.get(key)

    def update_paper_order_dispatch(self, dispatch, *, mutation_origin):
        if mutation_origin != "broker_reconciliation":
            raise ValueError("recovery_origin_blocked")
        self.rows[dispatch.order_plan_id] = dispatch
        return dispatch


def check_profile(store, kernel, client):
    if store.policy.data_mode != "paper_trading":
        raise ValueError("recovery_requires_paper_profile")
    if store.get("control") != "paused":
        raise ValueError("recovery_requires_paused_profile")
    if (store.get("account_binding") != client.account_scope_fingerprint
            or kernel.provenance.account_scope_fingerprint != client.account_scope_fingerprint):
        raise ValueError("recovery_account_mismatch")
    if kernel.provenance.data_mode != "paper_trading" or kernel.provenance.broker_environment != "kis_paper":
        raise ValueError("recovery_provenance_mismatch")


def result_summary(store, before, *, applied):
    after = {o["id"]: (o["state"], o["filled"], o["amount"]) for o in store.orders()}
    return {
        "status": "reconciled" if store.get("reconciliation_complete") else "blocked",
        "data_mode": "paper_trading", "applied": applied, "order_authority": False,
        "control": store.get("control"),
        "changed_orders": sum(before.get(key) != value for key, value in after.items()),
        "open_orders": len(store.orders(True)), "position_count": len(store.positions()),
        "position_quantity": sum(p["quantity"] for p in store.positions()),
        "reason": store.get("reconciliation_reason"),
        "diagnostics": [{k: v for k, v in d.items() if k != "order_plan_id"}
                        for d in store.get("reconciliation_diagnostics", [])],
    }


def preview(directory, client, calendar, now):
    directory = Path(directory)
    with ExitStack() as stack:
        exp = copy_memory(directory / "experiment.sqlite3")
        stack.callback(exp.close)
        broker = copy_memory(directory / "broker.sqlite3")
        stack.callback(broker.close)
        store = Store.__new__(Store)
        store.path, store.db = directory / "experiment.sqlite3", exp
        kernel = PreviewKernel(broker)
        check_profile(store, kernel, client)
        before = {o["id"]: (o["state"], o["filled"], o["amount"]) for o in store.orders()}
        gateway = KisGateway(store, client, calendar, lambda: now, kernel=kernel)
        gateway.reconcile(now)
        return result_summary(store, before, applied=False)


def backup_ledgers(directory):
    destination = directory / "recovery-backups" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=False)
    for name in ("experiment.sqlite3", "broker.sqlite3"):
        source = read_connection(directory / name)
        target = sqlite3.connect(destination / name)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    return destination


@contextmanager
def recovery_locks(directory, client):
    from quantpilot.paper.cli import process_lock

    with ExitStack() as stack:
        # Exactly the same locks as the runtime, including its collector thread.
        for role in ("trader", "worker", "reporter"):
            stack.enter_context(process_lock(directory / (role + ".lock")))
        lock_root = Path.home() / ".quantpilot" / "account-locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        key = sha256(client.account_scope_fingerprint.encode()).hexdigest()
        stack.enter_context(process_lock(lock_root / (key + ".lock")))
        yield


def apply_recovery(directory, client, calendar, now):
    directory = Path(directory).resolve()
    with recovery_locks(directory, client):
        proposed = preview(directory, client, calendar, now)
        if proposed["status"] != "reconciled":
            return proposed
        backup = backup_ledgers(directory)
        store = Store(directory / "experiment.sqlite3")
        gateway = None
        try:
            gateway = KisGateway(store, client, calendar, lambda: now)
            check_profile(store, gateway.kernel, client)
            before = {o["id"]: (o["state"], o["filled"], o["amount"]) for o in store.orders()}
            # Bridge the projection -> report transaction boundary across a restart.
            if proposed["changed_orders"] and not store.get("recovery_pending_since"):
                store.put("recovery_pending_since", now.isoformat())
            store.put("reconciliation_complete", False)
            gateway.reconcile(now)
            session = calendar.session(now)
            with store.transaction():
                for position in store.positions():
                    carried = datetime.fromisoformat(position["opened"]).astimezone(KST).date() < now.astimezone(KST).date()
                    if session is None or not session.trading(now) or carried:
                        store.db.execute("UPDATE positions SET quarantined=1 WHERE symbol=?", (position["symbol"],))
                roll_baselines(store, calendar, now)
            result = result_summary(store, before, applied=True)
            if result["status"] == "reconciled":
                with store.transaction():
                    store.put("incident", None)
                    if result["changed_orders"] or store.get("recovery_pending_since"):
                        store.put("valuation_invalidated_before", now.isoformat())
                        store.put("last_report_superseded", True)
                        from quantpilot.paper.reporting import snapshot
                        report = snapshot(store, now=now)
                        if session is not None and now >= session.closes:
                            record_close(store, now, valid=not report["valuation_incomplete"], equity=report["equity"])
                    store.audit("recovery_applied", result, now)
                    if store.get("last_report_superseded"):
                        from quantpilot.paper.reporting import enqueue_recovery_report
                        enqueue_recovery_report(store, now)
                    store.put("recovery_pending_since", None)
            result["backup_directory"] = str(backup)
            return result
        finally:
            if gateway is not None:
                gateway.close()
            store.close()
