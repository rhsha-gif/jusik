"""A just-closed minute bar is not final until the grace period has passed."""

from datetime import datetime, timedelta

import pytest

from quantpilot.paper.calendar import KST
from quantpilot.paper.config import BAR_FINALITY_GRACE_SECONDS
from quantpilot.paper.data import DataUnavailable, parse_minutes


def row(hhmm):
    return {
        "stck_bsop_date": "20260910",
        "stck_cntg_hour": hhmm + "00",
        "stck_oprc": "100",
        "stck_hgpr": "101",
        "stck_lwpr": "99",
        "stck_prpr": "100",
        "cntg_vol": "10",
    }


ROWS = [row("1000"), row("0959"), row("0958")]  # KIS returns newest first
BASE = datetime(2026, 9, 10, 10, 0, 0, tzinfo=KST)


def starts(now, **kwargs):
    return [b.start.astimezone(KST).strftime("%H:%M") for b in parse_minutes("005930", ROWS, now, **kwargs)]


def test_default_grace_is_fifteen_seconds():
    assert BAR_FINALITY_GRACE_SECONDS == 15


def test_bar_closed_less_than_grace_ago_is_still_forming():
    # 09:59 closed at 10:00:00; at 10:00:10 it is inside the grace window.
    assert starts(BASE + timedelta(seconds=10)) == ["09:58"]


def test_bar_becomes_final_once_grace_has_passed():
    assert starts(BASE + timedelta(seconds=15)) == ["09:58", "09:59"]
    assert starts(BASE + timedelta(seconds=59)) == ["09:58", "09:59"]


def test_forming_bar_never_appears_even_after_grace():
    assert "10:00" not in starts(BASE + timedelta(seconds=59))
    assert starts(BASE + timedelta(seconds=75)) == ["09:58", "09:59", "10:00"]


def test_zero_grace_reproduces_previous_behaviour():
    assert starts(BASE + timedelta(seconds=1), grace_seconds=0) == ["09:58", "09:59"]


def test_future_bar_is_rejected_regardless_of_grace():
    with pytest.raises(DataUnavailable):
        parse_minutes("005930", [row("1001")], BASE + timedelta(seconds=30))


def test_opening_minute_request_excludes_prior_day_rows():
    from quantpilot.packages.core.kis_paper import KisHttpResponse
    from quantpilot.paper.data import PaperMarket

    current = row("0900")
    previous = dict(row("1529"), stck_bsop_date="20260909")

    class Client:
        def _authenticated_get(self, endpoint, tr_id, *, params):
            rows = [current] if params["FID_PW_DATA_INCU_YN"] == "N" else [current, previous]
            return KisHttpResponse(200, {"rt_cd": "0", "output2": rows})

    now = BASE.replace(hour=9, minute=1, second=15)
    bars = PaperMarket(Client(), None).minutes("005930", now)
    assert [bar.start for bar in bars] == [now.replace(minute=0, second=0)]


def test_unexpected_prior_day_row_remains_rejected():
    previous = dict(row("1529"), stck_bsop_date="20260909")
    with pytest.raises(DataUnavailable, match="minute_payload_invalid"):
        parse_minutes("005930", [row("0959"), previous], BASE + timedelta(seconds=30))
