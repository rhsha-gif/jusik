"""Exchange schedule adapter; unknown/unsupported dates never imply an open market."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from quantpilot.paper.config import aware

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Session:
    opens: datetime
    closes: datetime

    def __post_init__(self):
        aware(self.opens)
        aware(self.closes)
        if self.closes <= self.opens:
            raise ValueError("invalid_session")

    def trading(self, now):
        return self.opens <= now < self.closes


class Calendar:
    def __init__(self, calendar=None):
        if calendar is None:
            import exchange_calendars as xcals

            calendar = xcals.get_calendar("XKRX")
        self.calendar = calendar

    def session(self, now):
        day = aware(now).astimezone(KST).date().isoformat()
        if not self.calendar.is_session(day):
            return None
        return Session(
            self.calendar.session_open(day).to_pydatetime(),
            self.calendar.session_close(day).to_pydatetime(),
        )

    def current_open_session_date(self, now):
        session = self.session(now)
        return now.astimezone(KST).date() if session and session.trading(now) else None

    def previous_session_date(self, now):
        day = aware(now).astimezone(KST).date().isoformat()
        return self.calendar.previous_session(day).date().isoformat()
