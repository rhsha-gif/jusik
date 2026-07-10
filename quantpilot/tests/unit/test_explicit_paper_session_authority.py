from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from quantpilot.packages.core.marketdata.paper_session import (
    ExplicitPaperTradingSessionAuthority,
)


KST = ZoneInfo("Asia/Seoul")
APPROVED_DATE = date(2026, 7, 10)


def test_returns_only_the_explicitly_approved_date_inside_safe_window() -> None:
    authority = ExplicitPaperTradingSessionAuthority(APPROVED_DATE)

    assert authority.approved_business_date == APPROVED_DATE
    assert (
        authority.current_open_session_date(
            datetime(2026, 7, 10, 9, 10, tzinfo=KST)
        )
        == APPROVED_DATE
    )
    assert (
        authority.current_open_session_date(
            datetime(2026, 7, 10, 15, 9, 59, tzinfo=KST)
        )
        == APPROVED_DATE
    )


def test_uses_seoul_local_date_instead_of_raw_utc_date() -> None:
    authority = ExplicitPaperTradingSessionAuthority(APPROVED_DATE)
    same_session_in_utc = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)

    assert authority.current_open_session_date(same_session_in_utc) == APPROVED_DATE


@pytest.mark.parametrize(
    "observed_at",
    [
        datetime(2026, 7, 10, 9, 9, 59, tzinfo=KST),
        datetime(2026, 7, 10, 15, 10, tzinfo=KST),
        datetime(2026, 7, 9, 10, 0, tzinfo=KST),
        datetime(2026, 7, 11, 10, 0, tzinfo=KST),
    ],
)
def test_outside_window_or_approved_local_date_returns_none(
    observed_at: datetime,
) -> None:
    authority = ExplicitPaperTradingSessionAuthority(APPROVED_DATE)

    assert authority.current_open_session_date(observed_at) is None


def test_weekend_is_never_treated_as_an_open_session() -> None:
    saturday = date(2026, 7, 11)
    authority = ExplicitPaperTradingSessionAuthority(saturday)

    assert (
        authority.current_open_session_date(
            datetime(2026, 7, 11, 10, 0, tzinfo=KST)
        )
        is None
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "2026-07-10",
        True,
        datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
    ],
)
def test_constructor_rejects_implicit_date_coercion(invalid: object) -> None:
    with pytest.raises(TypeError, match="must be a date"):
        ExplicitPaperTradingSessionAuthority(invalid)  # type: ignore[arg-type]


def test_observation_requires_an_aware_datetime() -> None:
    authority = ExplicitPaperTradingSessionAuthority(APPROVED_DATE)

    with pytest.raises(ValueError, match="UTC offset"):
        authority.current_open_session_date(datetime(2026, 7, 10, 10, 0))
    with pytest.raises(TypeError, match="must be a datetime"):
        authority.current_open_session_date(  # type: ignore[arg-type]
            "2026-07-10T10:00:00+09:00"
        )
