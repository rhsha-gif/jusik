"""Explicit paper-only market observation. This process has no order gateway."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import json
import threading

from quantpilot.paper.intraday.data import IntradayData


def register_paper_sources(data):
    for source, basis in (
        ("kis_paper_ranking", "collected_at"),
        ("kis_paper_minutes", "collected_at"),
        ("kis_paper_stream", "received_at"),
    ):
        data.register_source(
            source,
            {
                "provider": "kis_paper",
                "mode": "realtime_market_data",
                "license": "KIS account market-data terms; internal research only",
                "license_verified": False,
                "availability_basis": basis,
            },
        )


def collect_paper(
    path,
    seconds=60,
    experiment_path=None,
    runtime_path=None,
    *,
    env=None,
    security_master=None,
):
    from quantpilot.jobs.check_kis_paper_connection import connection_config
    from quantpilot.jobs.check_paper_readiness import (
        ReadinessTransport,
        readiness_market,
    )
    from quantpilot.jobs.run_intraday_backtest import external_path
    from quantpilot.jobs.collect_intraday_data import collect_once
    from quantpilot.packages.core.kis_paper import KisPaperClient
    from quantpilot.paper.auth import RefreshingClient
    from quantpilot.paper.calendar import Calendar
    from quantpilot.paper.config import environment_safe
    from quantpilot.paper.data import LimitedTransport
    from quantpilot.paper.intraday.stream import (
        PaperApprovalTransport,
        PaperStream,
        request_approval,
    )

    if type(seconds) is not int or not 1 <= seconds <= 24 * 3600:
        raise ValueError("bounded_collection_duration_required")
    env = os.environ if env is None else env
    if not environment_safe(env):
        raise ValueError("unsafe_environment")
    config = connection_config(env)
    clock = lambda: datetime.now(timezone.utc)
    calendar = Calendar()
    session = calendar.session(clock())
    if not session or not session.trading(clock()):
        return {"status": "pending_open_session", "order_authority": False}
    path = external_path(path)
    end = min(session.closes, clock() + timedelta(seconds=seconds))
    stop = threading.Event()
    transport = LimitedTransport(ReadinessTransport())
    client = RefreshingClient(
        config, lambda cfg: KisPaperClient(cfg, transport=transport), clock
    )
    market = readiness_market(client, calendar, clock)
    if security_master:
        master = json.loads(Path(security_master).read_text(encoding="utf-8"))
        from quantpilot.paper.intraday.evaluation import stamp

        as_of = stamp(master["as_of"])
        if (
            not master.get("source")
            or not master.get("license")
            or master.get("license_verified") is not True
            or not 0 <= (clock() - as_of).total_seconds() < 24 * 3600
        ):
            raise ValueError("verified_current_security_master_required")
        securities = {r["symbol"]: r for r in master["securities"]}
        market.eligibility = lambda symbols, at: {
            s: {
                **securities[s],
                "available_at": as_of.isoformat(),
                "source_verified": True,
                "source": master["source"],
            }
            for s in symbols
            if s in securities
        }
    failures = []
    subscribed = []
    subscription_lock = threading.Lock()

    def minute_loop():
        data = IntradayData(path, "realtime_market_data")
        try:
            while not stop.is_set() and clock() < end:
                result = collect_once(data, market, clock(), clock=clock)
                snapshots = data.dataset(since=session.opens, include_events=False)[
                    "universes"
                ]
                if snapshots:
                    with subscription_lock:
                        subscribed[:] = snapshots[-1]["symbols"][:20]
                if result["status"] != "collected":
                    failures.append("minute_collection_incomplete")
                if advisor:
                    advisor.offer(
                        data.dataset(since=session.opens, include_events=False),
                        clock(),
                        session,
                    )
                if runtime_path:
                    from quantpilot.paper.store import Store

                    cache = Store(
                        external_path(Path(runtime_path) / "experiment.sqlite3")
                    )
                    try:
                        snapshots = data.dataset(
                            since=session.opens, include_events=False
                        )["universes"]
                        if snapshots:
                            cache.put("intraday_universe", snapshots[-1])
                    finally:
                        cache.close()
                stop.wait(10)
        except Exception:
            failures.append("minute_collector_failed")
        finally:
            data.close()

    data = IntradayData(path, "realtime_market_data")
    thread = None
    runtime = None
    experiment = None
    advisor = None
    try:
        register_paper_sources(data)
        first = collect_once(data, market, clock(), clock=clock)
        if not first.get("universe_recorded"):
            return dict(first, order_authority=False)
        symbols = data.dataset(since=session.opens, include_events=False)["universes"][
            -1
        ]["symbols"][:20]
        subscribed[:] = symbols
        approval = request_approval(config, LimitedTransport(PaperApprovalTransport()))
        if runtime_path:
            from quantpilot.paper.store import Store

            runtime = Store(external_path(Path(runtime_path) / "experiment.sqlite3"))
            if runtime.policy.strategy_generation != "intraday_v2":
                raise ValueError("intraday_runtime_required")
        if experiment_path:
            from quantpilot.paper.intraday.evaluation import Experiment

            experiment = Experiment(external_path(experiment_path))
            from quantpilot.paper.intraday.advisor import ShadowAdvisor

            advisor = ShadowAdvisor(path.parent / "shadow-advisor.sqlite3", clock)
            advisor.start()
        thread = threading.Thread(
            target=minute_loop, name="intraday-read-only-minutes", daemon=True
        )
        thread.start()
        last_bucket = None
        last_observation_attempt = None

        def receive(event):
            nonlocal last_bucket, last_observation_attempt
            data.record_event(event)
            now = clock()
            if runtime:
                # Event normalization above is the authority boundary; persist a separate reader cache.
                runtime.put("intraday_feed_at", event["received_at"])
                runtime.put(
                    "intraday_event:" + event["symbol"] + ":" + event["kind"], event
                )
            bucket = now.replace(second=0, microsecond=0).isoformat()
            if (
                experiment
                and bucket != last_bucket
                and (
                    last_observation_attempt is None
                    or (now - last_observation_attempt).total_seconds() >= 5
                )
            ):
                from quantpilot.paper.intraday.shadow import observe

                last_observation_attempt = now
                if observe(
                    experiment,
                    data.dataset(since=session.opens, include_events=False),
                    now,
                    session,
                    advisor.assessment(now),
                ):
                    last_bucket = bucket

        def desired_symbols():
            with subscription_lock:
                return list(subscribed)

        PaperStream(approval, symbols).consume(
            receive, clock, lambda: stop.is_set() or clock() >= end, desired_symbols
        )
        if experiment and clock() >= session.closes:
            from quantpilot.paper.intraday.shadow import finalize

            finalize(experiment, data.dataset(since=session.opens), session, clock())
        snapshot = data.dataset()
        return {
            "status": "collected" if not failures else "partial",
            "order_authority": False,
            "bars": len(snapshot["bars"]),
            "events": len(snapshot["events"]),
            "sha256": snapshot["sha256"],
            "issues": sorted(set(failures)),
            "stream_universe": "point_in_time_ranking_subscriptions",
        }
    finally:
        stop.set()
        if thread:
            thread.join(timeout=30)
        if runtime:
            runtime.close()
        if advisor:
            advisor.close()
        if experiment:
            experiment.close()
        data.close()
