"""Explicit, fail-closed KRX paper-session authority."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from quantpilot.packages.core.execution.state_machine import (
    is_krx_auto_order_window,
)


KST = ZoneInfo("Asia/Seoul")


class ExplicitPaperTradingSessionAuthority:
    """Authorize one externally verified KRX business date.

    This class deliberately has no holiday inference.  A weekday is not
    assumed to be a KRX business day: ``approved_business_date`` must be
    supplied from externally or manually verified calendar evidence.  The
    authority then narrows that approval to the existing safe auto-order
    window and never rolls it forward to another date.
    """

    def __init__(self, approved_business_date: date) -> None:
        if type(approved_business_date) is not date:
            raise TypeError("approved KRX business date must be a date")
        self._approved_business_date = approved_business_date

    @property
    def approved_business_date(self) -> date:
        return self._approved_business_date

    def current_open_session_date(self, observed_at: datetime) -> date | None:
        if not isinstance(observed_at, datetime):
            raise TypeError("paper session observation must be a datetime")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("paper session observation must include a UTC offset")

        local = observed_at.astimezone(KST)
        if local.date() != self._approved_business_date:
            return None
        if local.weekday() >= 5:
            return None
        if not is_krx_auto_order_window(local):
            return None
        return self._approved_business_date
