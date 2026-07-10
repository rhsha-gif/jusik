from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quantpilot.packages.core.kis_paper import (
    KisL2Snapshot as KisApiL2Snapshot,
    KisOrderBookLevel,
    KisPaperTransportError,
)
from quantpilot.packages.core.marketdata.kis_paper import (
    KisPaperMarketDataProvider,
    KisPaperSnapshotUnavailable,
)
from quantpilot.packages.core.schemas import DataMode


NOW = datetime(2026, 7, 10, 1, 0, 10, tzinfo=timezone.utc)


def _book(
    symbol: str = "005930",
    *,
    accepted_at: str = "100005",
) -> KisApiL2Snapshot:
    return KisApiL2Snapshot(
        symbol=symbol,
        accepted_at_hhmmss=accepted_at,
        levels=tuple(
            KisOrderBookLevel(
                level=index,
                ask_price=Decimal(71200 + index * 100),
                bid_price=Decimal(71200 - index * 100),
                ask_quantity=100 * index,
                bid_quantity=200 * index,
            )
            for index in range(1, 11)
        ),
    )


class FakeSessionAuthority:
    def __init__(self, *open_dates: date) -> None:
        self.open_dates = set(open_dates or {date(2026, 7, 10)})
        self.error: Exception | None = None

    def current_open_session_date(self, observed_at: datetime) -> date | None:
        if self.error is not None:
            raise self.error
        local_date = observed_at.astimezone().date()
        # Tests run in any host timezone, so derive KST explicitly.
        kst_date = (observed_at + timedelta(hours=9)).date()
        assert local_date or True
        return kst_date if kst_date in self.open_dates else None


class FakeKisMarketClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.books: dict[str, KisApiL2Snapshot] = {
            "005930": _book(),
            "000660": _book("000660"),
        }
        self.errors: dict[str, Exception] = {}

    def get_current_price(self, *_args, **_kwargs):
        raise AssertionError("untimestamped current-price must not authorize a Quote")

    def get_l2(
        self,
        symbol: str,
        *,
        exchange: str = "KRX",
    ) -> KisApiL2Snapshot:
        self.calls.append(("l2", symbol, exchange))
        if symbol in self.errors:
            raise self.errors[symbol]
        return self.books[symbol]


class SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self._values = list(values)

    def __call__(self) -> datetime:
        if not self._values:
            raise AssertionError("clock read more times than expected")
        return self._values.pop(0)


def _provider(
    client: FakeKisMarketClient,
    *,
    clock=lambda: NOW,
    authority: FakeSessionAuthority | None = None,
) -> KisPaperMarketDataProvider:
    return KisPaperMarketDataProvider(
        client,
        session_authority=authority or FakeSessionAuthority(),
        clock=clock,
    )


def test_provider_uses_only_fresh_authorized_l2_midpoint() -> None:
    client = FakeKisMarketClient()
    provider = _provider(client)

    snapshot = provider.get_quotes(["005930", "005930"])

    assert client.calls == [("l2", "005930", "KRX")]
    assert snapshot.data_quality.usable is True
    assert snapshot.data_quality.data_mode == DataMode.paper_trading
    assert (
        snapshot.provider_status.reason
        == "kis_l2_midpoint_reference_authoritative_session"
    )
    assert snapshot.provider_status.observed_age_seconds == 5
    quote = snapshot.quotes["005930"]
    assert quote.last == 71200
    assert quote.bid == 71100
    assert quote.ask == 71300
    assert quote.as_of == datetime(2026, 7, 10, 10, 0, 5, tzinfo=quote.as_of.tzinfo)


@pytest.mark.parametrize(
    ("now", "accepted_at", "reason"),
    [
        (NOW, "095900", "kis_paper_snapshot_stale"),
        (NOW, "100012", "kis_paper_snapshot_from_future"),
        (
            datetime(2026, 7, 10, 23, 0, tzinfo=timezone.utc),
            "080000",
            "kis_paper_session_closed",
        ),
    ],
)
def test_stale_future_and_closed_session_snapshots_fail_closed(
    now: datetime,
    accepted_at: str,
    reason: str,
) -> None:
    client = FakeKisMarketClient()
    client.books["005930"] = _book(accepted_at=accepted_at)
    snapshot = _provider(client, clock=lambda: now).get_quotes(["005930"])

    assert snapshot.quotes == {}
    assert snapshot.data_quality.usable is False
    assert reason in snapshot.data_quality.reason_codes
    if reason == "kis_paper_session_closed":
        assert client.calls == []


def test_holiday_and_session_authority_failure_make_no_api_call() -> None:
    client = FakeKisMarketClient()
    holiday = FakeSessionAuthority(date(2026, 7, 9))

    closed = _provider(client, authority=holiday).get_quotes(["005930"])
    assert closed.data_quality.reason_codes == ["kis_paper_session_closed"]
    assert client.calls == []

    failing = FakeSessionAuthority()
    failing.error = RuntimeError("calendar backend secret must not leak")
    unavailable = _provider(client, authority=failing).get_quotes(["005930"])
    assert unavailable.data_quality.reason_codes == [
        "kis_paper_session_authority_unavailable"
    ]
    assert "calendar backend secret" not in unavailable.model_dump_json()
    assert client.calls == []


def test_slow_response_and_session_close_during_request_fail_closed() -> None:
    slow_client = FakeKisMarketClient()
    slow = _provider(
        slow_client,
        clock=SequenceClock(NOW, NOW + timedelta(seconds=40)),
    ).get_quotes(["005930"])
    assert slow.data_quality.reason_codes == [
        "kis_paper_snapshot_stale",
        "quote_missing",
    ]

    before_close = datetime(2026, 7, 10, 6, 29, 59, tzinfo=timezone.utc)
    close_client = FakeKisMarketClient()
    closed = _provider(
        close_client,
        clock=SequenceClock(before_close, before_close + timedelta(seconds=2)),
    ).get_quotes(["005930"])
    assert closed.data_quality.reason_codes == [
        "kis_paper_session_closed",
        "quote_missing",
    ]
    assert closed.quotes == {}


def test_client_failures_are_redacted_and_partial_quotes_are_removed() -> None:
    client = FakeKisMarketClient()
    client.errors["000660"] = KisPaperTransportError(
        "must not expose fake-account-123 or fake-secret-xyz"
    )
    snapshot = _provider(client).get_quotes(["005930", "000660"])

    rendered = snapshot.model_dump_json()
    assert snapshot.quotes == {}
    assert snapshot.data_quality.symbol_count == 0
    assert snapshot.data_quality.reason_codes == [
        "kis_paper_market_data_unavailable",
        "quote_missing",
    ]
    assert "fake-account-123" not in rendered
    assert "fake-secret-xyz" not in rendered


@pytest.mark.parametrize("failure", ["crossed", "bid_order", "ask_order", "depth"])
def test_crossed_inverted_and_incomplete_books_fail_closed(failure: str) -> None:
    client = FakeKisMarketClient()
    levels = list(_book().levels)
    if failure == "crossed":
        levels[0] = levels[0].__class__(
            level=1,
            ask_price=Decimal("71000"),
            bid_price=Decimal("71000"),
            ask_quantity=1,
            bid_quantity=1,
        )
    elif failure == "bid_order":
        levels[1] = levels[1].__class__(
            level=2,
            ask_price=levels[1].ask_price,
            bid_price=levels[0].bid_price,
            ask_quantity=1,
            bid_quantity=1,
        )
    elif failure == "ask_order":
        levels[1] = levels[1].__class__(
            level=2,
            ask_price=levels[0].ask_price,
            bid_price=levels[1].bid_price,
            ask_quantity=1,
            bid_quantity=1,
        )
    else:
        levels.pop()
    client.books["005930"] = KisApiL2Snapshot(
        symbol="005930",
        accepted_at_hhmmss="100005",
        levels=tuple(levels),
    )

    snapshot = _provider(client).get_quotes(["005930"])

    assert snapshot.quotes == {}
    assert "quote_missing" in snapshot.data_quality.reason_codes
    assert any("order_book" in item or "levels_invalid" in item for item in snapshot.data_quality.reason_codes)


def test_l2_adapter_preserves_validated_depth_and_rejects_stale_data() -> None:
    client = FakeKisMarketClient()
    provider = _provider(client)

    snapshot = provider.get_l2_snapshot("005930")

    assert snapshot.symbol == "005930"
    assert snapshot.bids[0] == {"price": 71100.0, "quantity": 200.0}
    assert snapshot.asks[0] == {"price": 71300.0, "quantity": 100.0}
    client.books["005930"] = _book(accepted_at="095900")
    with pytest.raises(KisPaperSnapshotUnavailable, match="snapshot_stale"):
        provider.get_l2_snapshot("005930")


def test_naive_clock_and_invalid_symbol_fail_without_network() -> None:
    client = FakeKisMarketClient()
    naive = _provider(client, clock=lambda: NOW.replace(tzinfo=None))

    result = naive.get_quotes(["005930"])
    assert result.data_quality.reason_codes == ["kis_paper_clock_naive"]
    with pytest.raises(ValueError, match="six alphanumeric"):
        naive.get_quotes(["not-a-symbol"])
    assert client.calls == []
