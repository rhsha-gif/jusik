"""Shared data contract between source adapters, the CSV writer, and the chart.

The contract is deliberately tiny: a source returns a list of :class:`Bar`
dicts, and everything downstream -- validation, CSV output, chart rendering --
consumes only that shape. A new asset class is therefore a new adapter, not a
new pipeline.
"""

from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable


class Bar(TypedDict):
    """One daily OHLCV bar.

    Field names and order match ``local_data/ohlcv.csv`` so bars written here
    can later be read by QuantPilot's ``DATA_MODE=local_historical`` providers
    without a mapping layer.

    ``date`` is a plain session date (``YYYY-MM-DD``) with no time or timezone
    suffix. Each adapter chooses its own session boundary and documents it; for
    24/7 markets this project uses UTC midnight so that crypto and overseas
    equity bars share one reference frame.

    ``volume`` is a float rather than an int because crypto quantities are
    fractional -- 222.567 BTC must not be truncated to 222.
    """

    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


BAR_FIELDS: tuple[str, ...] = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
"""CSV column order, identical to ``local_data/ohlcv.csv``."""


@runtime_checkable
class DailySource(Protocol):
    """Fetches daily bars for one symbol from one market-data provider.

    Implementations must satisfy three guarantees, because downstream code
    (CSV writing, chart x-axis spacing, indicator math) assumes all three:

    * bars are sorted by ``date`` ascending;
    * no two bars share a ``date``;
    * the returned list is complete -- a partial or failed fetch raises instead
      of returning early, so a broken download can never overwrite good data.
    """

    name: str

    def fetch_daily(self, symbol: str) -> list[Bar]: ...
