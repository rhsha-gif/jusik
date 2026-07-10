from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from quantpilot.packages.core.kis_paper import (
    KisL2Snapshot as KisApiL2Snapshot,
    KisPaperConfigurationError,
    KisPaperError,
)
from quantpilot.packages.core.marketdata.types import (
    L2Snapshot,
    MarketDataQuality,
    ProviderStatus,
    Quote,
    QuoteSnapshot,
)
from quantpilot.packages.core.schemas import DataMode, utc_now


KST = ZoneInfo("Asia/Seoul")
KRX_SESSION_OPEN = time(9, 0)
KRX_SESSION_CLOSE = time(15, 30)


class KisPaperSnapshotUnavailable(RuntimeError):
    """A REST snapshot cannot safely authorize a paper-trading decision."""


class KisPaperMarketDataClient(Protocol):
    def get_l2(
        self,
        symbol: str,
        *,
        exchange: str = "KRX",
    ) -> KisApiL2Snapshot: ...


class PaperTradingSessionAuthority(Protocol):
    """Authoritatively identifies an open KRX business session."""

    def current_open_session_date(self, observed_at: datetime) -> date | None: ...


class KisPaperMarketDataProvider:
    """Fail-closed adapter for explicitly injected KIS paper REST snapshots.

    KIS L2 exposes an exchange acceptance time but no business date, so an
    injected session authority is
    mandatory.  The executable reference is the timestamped L2 midpoint; the
    untimestamped REST current-price value is deliberately not used.
    """

    def __init__(
        self,
        client: KisPaperMarketDataClient,
        *,
        session_authority: PaperTradingSessionAuthority,
        clock: Callable[[], datetime] = utc_now,
        max_age_seconds: int = 30,
        future_tolerance_seconds: int = 1,
        provider_name: str = "kis_paper_rest_snapshot",
    ) -> None:
        if isinstance(max_age_seconds, bool) or max_age_seconds <= 0:
            raise ValueError("KIS paper snapshot max age must be positive")
        if (
            isinstance(future_tolerance_seconds, bool)
            or future_tolerance_seconds < 0
        ):
            raise ValueError("KIS paper future tolerance cannot be negative")
        if not provider_name.strip():
            raise ValueError("KIS paper provider name must not be blank")
        self._client = client
        self._session_authority = session_authority
        self._clock = clock
        self._max_age_seconds = max_age_seconds
        self._future_tolerance_seconds = future_tolerance_seconds
        self.provider_name = provider_name.strip()
        self.data_mode = DataMode.paper_trading

    def get_quotes(self, symbols: Sequence[str]) -> QuoteSnapshot:
        wanted = _normalize_symbols(symbols)
        try:
            observed_at = self._observed_at()
            session_date = self._require_regular_session(observed_at)
        except KisPaperSnapshotUnavailable as exc:
            return self._unavailable_quotes(wanted, str(exc))

        quotes: dict[str, Quote] = {}
        reason_codes: set[str] = set()
        for symbol in wanted:
            try:
                book = self._client.get_l2(symbol, exchange="KRX")
                received_at = self._observed_at()
                received_session_date = self._require_regular_session(received_at)
                if received_session_date != session_date:
                    raise KisPaperSnapshotUnavailable(
                        "kis_paper_session_changed_during_request"
                    )
                quotes[symbol] = self._quote_from_book(
                    symbol,
                    book=book,
                    observed_at=received_at,
                    session_date=received_session_date,
                )
                observed_at = max(observed_at, received_at)
            except KisPaperConfigurationError:
                reason_codes.add("kis_paper_configuration_error")
            except KisPaperSnapshotUnavailable as exc:
                reason_codes.add(str(exc))
            except KisPaperError:
                reason_codes.add("kis_paper_market_data_unavailable")
            except (ArithmeticError, TypeError, ValueError):
                reason_codes.add("kis_paper_snapshot_invalid")

        missing = sorted(set(wanted) - set(quotes))
        if missing:
            reason_codes.add("quote_missing")
        usable = bool(wanted) and not reason_codes
        if not wanted:
            reason_codes.add("quote_symbols_empty")
        ordered_reasons = sorted(reason_codes)
        if not usable:
            quotes = {}
        return QuoteSnapshot(
            quotes=quotes,
            provider_status=ProviderStatus(
                provider_name=self.provider_name,
                state="available" if usable else "unavailable",
                data_mode=self.data_mode,
                reason=(
                    "kis_l2_midpoint_reference_authoritative_session"
                    if usable
                    else ", ".join(ordered_reasons)
                ),
                observed_at=observed_at,
                as_of=min((item.as_of for item in quotes.values()), default=None),
                stale_after_seconds=self._max_age_seconds,
                observed_age_seconds=max(
                    (
                        (observed_at - item.as_of).total_seconds()
                        for item in quotes.values()
                    ),
                    default=None,
                ),
            ),
            data_quality=MarketDataQuality(
                usable=usable,
                degraded=not usable,
                reason_codes=ordered_reasons,
                symbol_count=len(quotes),
                data_mode=self.data_mode,
            ),
        )

    def get_l2_snapshot(self, symbol: str) -> L2Snapshot:
        normalized = _normalize_symbols([symbol])[0]
        request_started_at = self._observed_at()
        session_date = self._require_regular_session(request_started_at)
        try:
            book = self._client.get_l2(normalized, exchange="KRX")
        except KisPaperError as exc:
            raise KisPaperSnapshotUnavailable(
                "kis_paper_market_data_unavailable"
            ) from exc
        observed_at = self._observed_at()
        received_session_date = self._require_regular_session(observed_at)
        if received_session_date != session_date:
            raise KisPaperSnapshotUnavailable(
                "kis_paper_session_changed_during_request"
            )
        as_of = self._snapshot_time(
            book,
            observed_at=observed_at,
            session_date=received_session_date,
        )
        if book.symbol != normalized:
            raise KisPaperSnapshotUnavailable("kis_paper_symbol_mismatch")
        levels = _validated_levels(book)
        bids = [
            {
                "price": _positive_float(level.bid_price),
                "quantity": _non_negative_float(level.bid_quantity),
            }
            for level in levels
        ]
        asks = [
            {
                "price": _positive_float(level.ask_price),
                "quantity": _non_negative_float(level.ask_quantity),
            }
            for level in levels
        ]
        if not bids or not asks:
            raise KisPaperSnapshotUnavailable("kis_paper_order_book_empty")
        return L2Snapshot(symbol=normalized, bids=bids, asks=asks, as_of=as_of)

    def _quote_from_book(
        self,
        symbol: str,
        *,
        book: KisApiL2Snapshot,
        observed_at: datetime,
        session_date: date,
    ) -> Quote:
        if book.symbol != symbol:
            raise KisPaperSnapshotUnavailable("kis_paper_symbol_mismatch")
        levels = _validated_levels(book)
        top = levels[0]
        bid = _positive_float(top.bid_price)
        ask = _positive_float(top.ask_price)
        return Quote(
            symbol=symbol,
            last=(bid + ask) / 2,
            bid=bid,
            ask=ask,
            as_of=self._snapshot_time(
                book,
                observed_at=observed_at,
                session_date=session_date,
            ),
        )

    def _observed_at(self) -> datetime:
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise KisPaperSnapshotUnavailable("kis_paper_clock_naive")
        return observed_at

    def _require_regular_session(self, observed_at: datetime) -> date:
        local = observed_at.astimezone(KST)
        local_time = local.time().replace(tzinfo=None)
        if not KRX_SESSION_OPEN <= local_time <= KRX_SESSION_CLOSE:
            raise KisPaperSnapshotUnavailable("kis_paper_session_closed")
        try:
            session_date = self._session_authority.current_open_session_date(
                observed_at
            )
        except Exception as exc:
            raise KisPaperSnapshotUnavailable(
                "kis_paper_session_authority_unavailable"
            ) from exc
        if session_date != local.date():
            raise KisPaperSnapshotUnavailable("kis_paper_session_closed")
        return session_date

    def _snapshot_time(
        self,
        book: KisApiL2Snapshot,
        *,
        observed_at: datetime,
        session_date: date,
    ) -> datetime:
        raw = book.accepted_at_hhmmss
        try:
            accepted_time = time(
                int(raw[0:2]),
                int(raw[2:4]),
                int(raw[4:6]),
            )
        except (TypeError, ValueError):
            raise KisPaperSnapshotUnavailable("kis_paper_snapshot_time_invalid") from None
        local_observed = observed_at.astimezone(KST)
        accepted_at = datetime.combine(
            session_date,
            accepted_time,
            tzinfo=KST,
        )
        age_seconds = (local_observed - accepted_at).total_seconds()
        if age_seconds < -self._future_tolerance_seconds:
            raise KisPaperSnapshotUnavailable("kis_paper_snapshot_from_future")
        if age_seconds > self._max_age_seconds:
            raise KisPaperSnapshotUnavailable("kis_paper_snapshot_stale")
        return accepted_at

    def _unavailable_quotes(
        self,
        symbols: Sequence[str],
        reason_code: str,
    ) -> QuoteSnapshot:
        return QuoteSnapshot(
            quotes={},
            provider_status=ProviderStatus(
                provider_name=self.provider_name,
                state="unavailable",
                data_mode=self.data_mode,
                reason=reason_code,
                stale_after_seconds=self._max_age_seconds,
            ),
            data_quality=MarketDataQuality(
                usable=False,
                degraded=True,
                reason_codes=[reason_code],
                symbol_count=0,
                data_mode=self.data_mode,
            ),
        )


def _normalize_symbols(symbols: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = str(raw).strip().upper()
        if len(symbol) != 6 or not symbol.isalnum():
            raise ValueError("KIS paper symbols must be six alphanumeric characters")
        if symbol not in seen:
            seen.add(symbol)
            normalized.append(symbol)
    return normalized


def _positive_float(value: Decimal | int) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise ValueError("market-data price must be positive")
    return parsed


def _non_negative_float(value: Decimal | int) -> float:
    parsed = float(value)
    if parsed < 0:
        raise ValueError("market-data quantity cannot be negative")
    return parsed


def _validated_levels(book: KisApiL2Snapshot):
    levels = sorted(book.levels, key=lambda item: item.level)
    if len(levels) != 10 or [item.level for item in levels] != list(range(1, 11)):
        raise KisPaperSnapshotUnavailable("kis_paper_order_book_depth_invalid")
    bids = [_positive_float(item.bid_price) for item in levels]
    asks = [_positive_float(item.ask_price) for item in levels]
    if bids[0] >= asks[0]:
        raise KisPaperSnapshotUnavailable("kis_paper_order_book_crossed")
    if any(left <= right for left, right in zip(bids, bids[1:])):
        raise KisPaperSnapshotUnavailable("kis_paper_bid_levels_invalid")
    if any(left >= right for left, right in zip(asks, asks[1:])):
        raise KisPaperSnapshotUnavailable("kis_paper_ask_levels_invalid")
    return levels
