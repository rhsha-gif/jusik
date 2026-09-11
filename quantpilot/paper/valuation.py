"""A previous observation is not automatically the previous session's close."""

from datetime import datetime
from quantpilot.paper.calendar import KST


def roll_baselines(store, calendar, now):
    day = now.astimezone(KST).date().isoformat()
    previous_day = store.get("day")
    initial = store.get("initial_capital")
    first_session = all(datetime.fromisoformat(o["at"]).astimezone(KST).date().isoformat() == day
                        for o in store.orders())
    try:
        expected_close = calendar.previous_session_date(now)
    except (AttributeError, ValueError):
        expected_close = None
    close_valid = (expected_close is not None and store.get("last_close_day") == expected_close
                   and store.get("last_close_equity_valid") is True)
    if previous_day != day:
        store.put("day", day)
        store.put("day_base", initial if first_session else store.get("last_close_equity", initial))
        store.put("day_base_valid", first_session or close_valid)
        store.put("day_base_source", "initial_capital" if first_session else expected_close)
    elif store.get("day_base_valid") is None:
        # Old profiles may only prove an initial-capital baseline on their first day.
        store.put("day_base_valid", first_session and store.get("day_base", initial) == initial)
        store.put("day_base_source", "initial_capital" if first_session else None)
    if store.get("month") != day[:7]:
        store.put("month", day[:7])
        store.put("month_base", store.get("last_close_equity", initial))


def record_close(store, now, *, valid, equity=None):
    store.put("last_close_day", now.astimezone(KST).date().isoformat())
    store.put("last_close_observed_at", now.isoformat())
    store.put("last_close_equity_valid", valid)
    if valid:
        store.put("last_close_equity", equity)
