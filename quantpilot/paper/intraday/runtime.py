"""Frozen strategy selection and bounded advisory budgeting for the existing runtime."""

from datetime import datetime, timedelta

from quantpilot.paper.intraday.deployment import admitted
from quantpilot.paper.intraday.strategy import evaluate, get_spec, rank
from quantpilot.paper.strategy import _atr, aggregate_five_minutes, validate_bars


def signals_for(store, bars, now, session_open):
    signals = []
    for name, record in store.get("intraday_admission", {}).items():
        if name in store.policy.active_strategies and admitted(
            store, name, record.get("version")
        ):
            signals.extend(
                evaluate(
                    bars,
                    now,
                    session_open,
                    get_spec(record["version"]),
                    record["candidate"]["calibration"],
                )
            )
    return signals


def allocate(signals, assessment, cap=0.6):
    scores = {}
    for signal in signals:
        # Undo the monotone expected-R -> unit-interval score transform.
        expected = signal.score / max(1e-12, 1 - signal.score)
        scores[signal.strategy_id] = max(scores.get(signal.strategy_id, 0), expected)
    total = sum(scores.values())
    base = (
        {name: min(cap, value / total) for name, value in scores.items()}
        if total
        else {}
    )
    return {
        name: (
            min(cap, value * (0.8 + 0.4 * assessment.strategy_scores.get(name, 0.5)))
            if assessment
            else value
        )
        for name, value in base.items()
    }


def schedule(store, histories, now, session):
    """30-minute regular updates and coalesced, rate-limited completed-bar events."""
    if not session.trading(now):
        return
    old = store.get("intraday_ai_schedule", {})
    slot = int((now - session.opens).total_seconds() // 1800)
    regular_key = session.opens.date().isoformat() + ":" + str(slot)
    event_symbols = []
    for symbol, bars in histories.items():
        try:
            five = aggregate_five_minutes(validate_bars(bars, now))
            if len(five) >= 16 and five[-1].start + timedelta(minutes=5) <= now:
                atr = _atr(five[:-1])
                if atr and abs(five[-1].close - five[-2].close) > 2 * atr:
                    event_symbols.append(symbol)
        except ValueError:
            continue
    event_key = now.replace(
        minute=now.minute // 5 * 5, second=0, microsecond=0
    ).isoformat()
    event = len(event_symbols) >= 2 and old.get("event_key") != event_key
    regular = old.get("regular_key") != regular_key
    last = datetime.fromisoformat(old["at"]) if old.get("at") else None
    if (regular or event) and (last is None or (now - last).total_seconds() >= 600):
        key = "intraday:" + now.isoformat()
        store.put("ai_due", {"kind": "intraday", "key": key})
        store.put(
            "intraday_ai_schedule",
            {"at": now.isoformat(), "regular_key": regular_key, "event_key": event_key},
        )
        store.audit(
            "intraday_advisory_scheduled",
            {"job": key, "regular": regular, "event_symbols": event_symbols},
            now,
        )


def record_market_event(store, event, now):
    """Validated, received market events grant freshness, never order authority."""
    from quantpilot.paper.intraday.data import IntradayData
    from quantpilot.paper.config import aware

    received = aware(datetime.fromisoformat(event["received_at"]))
    occurred = aware(datetime.fromisoformat(event["event_at"]))
    if (
        not 0 <= (now - received).total_seconds() < store.policy.quote_ttl_seconds
        or not 0
        <= (received - occurred).total_seconds()
        < store.policy.quote_ttl_seconds
    ):
        raise ValueError("intraday_market_event_stale")
    data = IntradayData(
        store.path.parent / "intraday-market.sqlite3", "realtime_market_data"
    )
    try:
        from quantpilot.paper.intraday.collection import register_paper_sources

        register_paper_sources(data)
        data.record_event(event)
    finally:
        data.close()
    store.put("intraday_feed_at", received.isoformat())
    store.put("intraday_event:" + event["symbol"] + ":" + event["kind"], event)
