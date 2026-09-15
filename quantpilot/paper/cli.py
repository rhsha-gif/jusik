"""Shared operator CLI for humans, Codex and Claude Code. No web server required."""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from contextlib import contextmanager

from quantpilot.paper.config import environment_safe
from quantpilot.paper.store import Store
from quantpilot.paper.reporting import snapshot


@contextmanager
def process_lock(path):
    handle = open(path, "a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


def build_runtime(store, env):
    if store.get("recovery_pending_since"):
        raise ValueError("recovery_incomplete")
    if not environment_safe(env):
        raise ValueError("unsafe_environment")
    if store.policy.data_mode != "paper_trading":
        raise ValueError("fixture_requires_injected_test_clients")
    if env.get("KIS_PAPER_ORDER_SUBMISSION_ENABLED", "false").lower() != "true":
        raise ValueError("paper_submission_disabled")
    from quantpilot.paper.calendar import Calendar
    from quantpilot.paper.data import PaperMarket, LimitedTransport
    from quantpilot.paper.broker import KisGateway
    from quantpilot.paper.runtime import Runtime
    from quantpilot.jobs.check_kis_paper_connection import connection_config
    from quantpilot.packages.core.kis_paper import (
        KisPaperClient,
        StrictUrllibKisPaperTransport,
    )
    from quantpilot.packages.core.marketdata.kis_paper import KisPaperMarketDataProvider

    calendar = Calendar()
    config = connection_config(env)
    from quantpilot.paper.transport import shared_transport
    transport = shared_transport(config, StrictUrllibKisPaperTransport(), enabled=store.policy.shared_api_budget_enabled)
    clock = lambda: datetime.now(timezone.utc)
    from quantpilot.paper.auth import RefreshingClient

    client = RefreshingClient(
        config, lambda cfg: KisPaperClient(cfg, transport=transport), clock
    )
    quotes = KisPaperMarketDataProvider(client, session_authority=calendar, clock=clock)
    gateway = KisGateway(store, client, calendar, clock)
    runtime = Runtime(
        store,
        PaperMarket(client, quotes),
        gateway,
        calendar,
        clock,
        env,
        background_data=True,
    )
    runtime.budget = getattr(transport, "budget", None)
    if runtime.budget:
        store.put("api_budget_location", {"path": str(runtime.budget.path), "scope": runtime.budget.scope})
    gateway.budget = runtime.budget
    gateway.environment = env
    runtime.market.clock = clock
    if store.policy.hybrid_feed_enabled:
        from quantpilot.paper.feeds import FeedApproval, HybridFeed
        from quantpilot.paper.intraday.stream import PaperApprovalTransport, request_approval
        approval_transport = shared_transport(config, PaperApprovalTransport(), enabled=store.policy.shared_api_budget_enabled)
        def approval():
            notice_key = env.get("KIS_PAPER_HTS_ID", "").strip()
            if not notice_key:
                raise ValueError("paper_notice_key_missing")
            return FeedApproval(request_approval(config, approval_transport), notice_key)
        runtime.feed = HybridFeed(connect=None, approval_supplier=approval, clock=clock,
                                  reconciliation_seconds=65)
        runtime.market.feed = runtime.feed
        gateway.feed = runtime.feed
    return runtime


@contextmanager
def account_owner(runtime):
    from hashlib import sha256

    directory = Path.home() / ".quantpilot" / "account-locks"
    directory.mkdir(parents=True, exist_ok=True)
    key = sha256(runtime.gateway.client.account_scope_fingerprint.encode()).hexdigest()
    with process_lock(directory / (key + ".lock")):
        runtime.gateway.recover_exclusive_owner()
        yield


def parser():
    p = argparse.ArgumentParser(
        description="QuantPilot paper experiment (disabled by default)"
    )
    p.add_argument(
        "--runtime-dir", type=Path, default=Path.home() / ".quantpilot" / "intraday"
    )
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("status", "pause", "resume", "flatten", "report", "strategies"):
        sub.add_parser(command)
    start = sub.add_parser("start")
    start.add_argument("--once", action="store_true")
    start.add_argument("--supervised", action="store_true")
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    reporter = sub.add_parser("reporter")
    reporter.add_argument("--once", action="store_true")
    reconciliation = sub.add_parser("reconcile")
    reconciliation.add_argument("--apply", action="store_true")
    reconciliation.add_argument("--dry-run", action="store_true")
    conf = sub.add_parser("config")
    conf.add_argument("--set", dest="changes")
    conf.add_argument("--expected-version", type=int)
    review = sub.add_parser("review-drawdown")
    review.add_argument("--reason", required=True)
    recancel = sub.add_parser("recancel")
    recancel.add_argument("--order", required=True)
    resolve = sub.add_parser("resolve-unknown")
    resolve.add_argument("--order", required=True)
    resolve.add_argument("--reason", required=True)
    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--port", type=int, default=8770)
    dashboard.add_argument("--sample-seconds", type=float, default=10)
    stable = sub.add_parser("stabilize")
    stable.add_argument("--apply", action="store_true")
    stable.add_argument("--expected-version", type=int)
    accept = sub.add_parser("acceptance")
    accept.add_argument("--start-day", required=True)
    month = sub.add_parser("month-baseline")
    month.add_argument("--apply", action="store_true")
    month.add_argument("--expected-version", type=int)
    month.add_argument("--reason")
    return p


def blocked_result(exc):
    # Never print transport/provider/config exception strings containing external payloads.
    result = {"status": "blocked", "reason": type(exc).__name__}
    if (
        isinstance(exc, ValueError)
        and len(str(exc)) < 100
        and str(exc).replace("_", "").isalnum()
    ):
        result["reason"] = str(exc)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    directory = args.runtime_dir.expanduser().resolve()
    # Runtime DBs must not land in source control, including another checkout of this repository.
    if any((p / ".git").exists() for p in (directory, *directory.parents)):
        print(
            json.dumps(
                {"status": "blocked", "reason": "runtime_directory_inside_repository"}
            )
        )
        return 2
    if args.command in {"status", "report", "acceptance", "stabilize", "month-baseline"}:
        # Inspection of old ledgers never triggers an additive migration.
        from quantpilot.paper.dashboard import ledger
        from quantpilot.paper.reporting import render
        try:
            if args.command == "month-baseline":
                from quantpilot.paper.valuation import month_baseline_preview, apply_month_baseline
                at = datetime.now(timezone.utc)
                if args.apply:
                    if args.expected_version is None:
                        raise ValueError("expected_version_required")
                    result = apply_month_baseline(directory, at, args.expected_version, args.reason)
                else:
                    with ledger(directory / "experiment.sqlite3") as view:
                        result = month_baseline_preview(view, at)
            elif args.command == "stabilize":
                from quantpilot.paper.stabilization import preview, apply
                if args.apply and args.expected_version is None:
                    raise ValueError("expected_version_required")
                result = apply(directory, os.environ, args.expected_version) if args.apply else preview(directory)
            else:
                with ledger(directory / "experiment.sqlite3") as view:
                    if args.command == "acceptance":
                        from quantpilot.paper.stabilization import acceptance
                        from quantpilot.paper.calendar import Calendar
                        result = acceptance(view, Calendar(), args.start_day, datetime.now(timezone.utc))
                    else:
                        result = snapshot(view)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False) if args.json or args.command not in {"status", "report"} else render(result))
            return 0 if result.get("status") != "blocked" else 2
        except Exception as exc:
            print(json.dumps(blocked_result(exc)))
            return 2
    if args.command == "reconcile":
        from quantpilot.paper.recovery import build_client, preview, apply_recovery
        from quantpilot.paper.calendar import Calendar

        try:
            if args.apply and args.dry_run:
                raise ValueError("conflicting_recovery_modes")
            clock = lambda: datetime.now(timezone.utc)
            client = build_client(os.environ, clock)
            result = (apply_recovery if args.apply else preview)(directory, client, Calendar(), clock())
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0 if result["status"] == "reconciled" else 2
        except Exception as exc:
            print(json.dumps(blocked_result(exc)))
            return 2
    if args.command == "dashboard":
        # Read-only viewer: never constructs Store (a writer) and takes no trader lock.
        from quantpilot.paper.dashboard import serve

        try:
            serve(directory, args.port, args.sample_seconds)
        except KeyboardInterrupt:
            print(json.dumps({"status": "dashboard_stopped"}))
            return 130
        except Exception as exc:
            print(json.dumps(blocked_result(exc)))
            return 2
        return 0
    store = Store(directory / "experiment.sqlite3")
    try:
        if args.command in {"status", "report"}:
            result = snapshot(store)
        elif args.command == "config":
            if args.changes:
                if args.expected_version is None:
                    raise ValueError("expected_version_required")
                result = store.configure(
                    json.loads(args.changes), args.expected_version
                ).model_dump(mode="json")
            else:
                result = store.policy.model_dump(mode="json")
        elif args.command == "strategies":
            result = {
                "active": list(store.policy.active_strategies),
                "weights": store.get("weights", {}),
                "lab": [
                    json.loads(r[0]) for r in store.db.execute("SELECT body FROM lab")
                ],
            }
        elif args.command == "review-drawdown":
            store.review_intraday_halt(args.reason, datetime.now(timezone.utc))
            result = {
                "control": store.get("control"),
                "drawdown_reviewed": True,
                "orders_armed": False,
            }
        elif args.command in {"pause", "resume", "flatten"}:
            store.control(args.command)
            result = {"control": store.get("control"), "completion": "requested"}
        elif args.command in {"recancel", "resolve-unknown"}:
            from quantpilot.paper.operator import (
                release_cancel_claim,
                resolve_unknown_dispatch,
            )
            from quantpilot.paper.recovery import build_client

            clock = lambda: datetime.now(timezone.utc)
            # Query-only transport: token, balance and daily orders. No order POST.
            client = build_client(os.environ, clock)
            if args.command == "recancel":
                result = release_cancel_claim(store, client, args.order, clock())
            else:
                try:
                    lock = process_lock(directory / "trader.lock")
                    lock.__enter__()
                except OSError:
                    raise ValueError("trader_running") from None
                try:
                    result = resolve_unknown_dispatch(
                        store, client, args.order, args.reason, clock()
                    )
                finally:
                    lock.__exit__(None, None, None)
        elif args.command == "reporter":
            from quantpilot.paper.reporting import (
                SlackDM,
                check_trader_liveness,
                drain_outbox, operation_report_once,
            )

            from quantpilot.paper.calendar import Calendar
            calendar = Calendar()
            with process_lock(directory / "reporter.lock"):
                if not store.get("supervised:reporter"):
                    store.put("role_stop:reporter", False)
                store.db.execute(
                    "UPDATE outbox SET state='delivery_unknown',error='reporter_interrupted' WHERE state='sending'"
                )
                while True:
                    if store.get("role_stop:reporter"):
                        result = {"status": "reporter_stopped"}
                        break
                    at = datetime.now(timezone.utc)
                    store.put("reporter_heartbeat", at.isoformat())
                    operation_report_once(store, calendar, at)
                    check_trader_liveness(store, at, calendar)
                    if store.policy.slack_enabled:
                        drain_outbox(store, SlackDM(os.environ))
                    result = {"status": "reporter_ready"}
                    if args.once:
                        break
                    time.sleep(1)
        elif args.command == "worker":
            from quantpilot.paper.jobs import work_once, recover_interrupted_jobs

            with process_lock(directory / "worker.lock"):
                if not store.get("supervised:worker"):
                    store.put("role_stop:worker", False)
                from quantpilot.paper.intelligence import configure_runners
                store.put("ai_runners", configure_runners())
                def heartbeat_runner(provider, prompt, schema):
                    from quantpilot.paper.intelligence import default_cli_runner
                    store.put("worker_heartbeat", datetime.now(timezone.utc).isoformat())
                    return default_cli_runner(provider, prompt, schema)
                recover_interrupted_jobs(store)
                while True:
                    if store.get("role_stop:worker"):
                        result = {"status": "worker_stopped"}
                        break
                    store.put("worker_heartbeat", datetime.now(timezone.utc).isoformat())
                    result = work_once(store, runner=heartbeat_runner)
                    if args.once:
                        break
                    time.sleep(10)
        else:
            with process_lock(directory / "trader.lock"):
                if not store.get("supervised:trader"):
                    store.put("role_stop:trader", False)
                runtime = build_runtime(store, os.environ)
                try:
                    with account_owner(runtime):
                        from quantpilot.paper.collector import Collector

                        collector = Collector(
                            store.path, runtime.market, runtime.calendar, runtime.clock
                        )
                        collector.start()
                        feed_service = None
                        if runtime.feed:
                            from quantpilot.paper.feed_service import FeedService
                            feed_service = FeedService(store.path, runtime.feed, runtime.clock)
                            feed_service.start()
                        if store.policy.supervisor_enabled and not store.get("recovery_required"):
                            store.put("recovery_required", True)
                        # Starting an already-paused service never silently resumes new entries.
                        if store.get("control") == "stopped" and not store.policy.supervisor_enabled:
                            store.control("start")
                        try:
                            while True:
                                if store.get("role_stop:trader"):
                                    result = {"status": "trader_stopped"}
                                    break
                                from quantpilot.paper.calendar import KST
                                at = runtime.clock()
                                store.put("heartbeat", at.isoformat())
                                day = at.astimezone(KST).date().isoformat()
                                session = runtime.calendar.session(at)
                                if session and store.get("session_coverage:" + day) is None:
                                    store.put("session_coverage:" + day, {"started_before_open": at <= session.opens, "first_heartbeat": at.isoformat()})
                                result = runtime.cycle()
                                if runtime.budget:
                                    budget_status = runtime.budget.snapshot()
                                    today = budget_status.get("days", {}).get(day, {})
                                    store.put("api_budget_status", dict(budget_status, state="enabled", violations=today.get("rate_violations", 0)))
                                if args.once:
                                    break
                                # Notice hints and stop requests interrupt the ordinary cycle wait.
                                wake = store.get("reconcile_wakeup")
                                deadline = time.monotonic() + store.policy.cycle_seconds
                                while time.monotonic() < deadline:
                                    if store.get("role_stop:trader") or store.get("reconcile_wakeup") != wake:
                                        break
                                    time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                        finally:
                            collector.close()
                            if feed_service:
                                feed_service.close()
                finally:
                    runtime.gateway.close()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        elif args.command in {"status", "report"}:
            from quantpilot.paper.reporting import render

            print(render(result))
        else:
            print(
                "\n".join(
                    f"{key}: {json.dumps(value,ensure_ascii=False,allow_nan=False)}"
                    for key, value in result.items()
                )
            )
        return 0
    except KeyboardInterrupt:
        print(
            json.dumps(
                {"status": "process_interrupted", "control": store.get("control")}
            )
        )
        return 130
    except Exception as exc:
        result = blocked_result(exc)
        if args.command in {"start", "worker", "reporter"}:
            # Hidden processes have no console; the ledger is the only place a
            # refused start or a crash can be seen.
            try:
                from quantpilot.paper.reporting import record_process_failure

                record_process_failure(store, args.command, result)
            except Exception:
                pass
        print(json.dumps(result))
        return 2
    finally:
        store.close()
