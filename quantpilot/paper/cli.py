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
    transport = LimitedTransport(StrictUrllibKisPaperTransport())
    clock = lambda: datetime.now(timezone.utc)
    from quantpilot.paper.auth import RefreshingClient

    client = RefreshingClient(
        config, lambda cfg: KisPaperClient(cfg, transport=transport), clock
    )
    quotes = KisPaperMarketDataProvider(client, session_authority=calendar, clock=clock)
    gateway = KisGateway(store, client, calendar, clock)
    return Runtime(
        store,
        PaperMarket(client, quotes),
        gateway,
        calendar,
        clock,
        env,
        background_data=True,
    )


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
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    reporter = sub.add_parser("reporter")
    reporter.add_argument("--once", action="store_true")
    conf = sub.add_parser("config")
    conf.add_argument("--set", dest="changes")
    conf.add_argument("--expected-version", type=int)
    review = sub.add_parser("review-drawdown")
    review.add_argument("--reason", required=True)
    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--port", type=int, default=8770)
    dashboard.add_argument("--sample-seconds", type=float, default=10)
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
        elif args.command == "reporter":
            from quantpilot.paper.reporting import SlackDM, drain_outbox

            with process_lock(directory / "reporter.lock"):
                store.db.execute(
                    "UPDATE outbox SET state='delivery_unknown',error='reporter_interrupted' WHERE state='sending'"
                )
                while True:
                    if store.policy.slack_enabled:
                        drain_outbox(store, SlackDM(os.environ))
                    result = {"status": "reporter_ready"}
                    if args.once:
                        break
                    time.sleep(1)
        elif args.command == "worker":
            from quantpilot.paper.jobs import work_once, recover_interrupted_jobs

            with process_lock(directory / "worker.lock"):
                recover_interrupted_jobs(store)
                while True:
                    result = work_once(store)
                    if args.once:
                        break
                    time.sleep(10)
        else:
            with process_lock(directory / "trader.lock"):
                runtime = build_runtime(store, os.environ)
                try:
                    with account_owner(runtime):
                        from quantpilot.paper.collector import Collector

                        collector = Collector(
                            store.path, runtime.market, runtime.calendar, runtime.clock
                        )
                        collector.start()
                        # Starting an already-paused service never silently resumes new entries.
                        if store.get("control") == "stopped":
                            store.control("start")
                        try:
                            while True:
                                result = runtime.cycle()
                                if args.once:
                                    break
                                time.sleep(store.policy.cycle_seconds)
                        finally:
                            collector.close()
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
        print(json.dumps(blocked_result(exc)))
        return 2
    finally:
        store.close()
