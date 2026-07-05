"""Fetch real KRX daily OHLCV into ``local_historical`` CSV files.

This job downloads public reference/historical data only. It never touches a
broker, never reads credentials, and never changes how orders are placed. The
output directory is immediately re-loaded through the same fail-closed
``CsvSecurityProvider`` / ``CsvMarketDataProvider`` used by
``DATA_MODE=local_historical``, so a successful run guarantees the files are
consumable by the harness.

Usage:

    python -m quantpilot.jobs.fetch_krx_local_data \
        --symbols 005930,000660 \
        --start 2025-06-01 --end 2026-07-03 \
        --out-dir local_data \
        --sectors 005930=technology,000660=technology

Requires the optional ``pykrx`` package (``pip install pykrx``); the import is
lazy so the rest of the test suite never needs it.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from quantpilot.packages.core.data.providers import ProviderError, build_providers
from quantpilot.packages.core.schemas import DataMode

_OHLCV_COLUMNS: list[str] = ["symbol", "date", "open", "high", "low", "close", "volume"]
_SECURITY_COLUMNS: list[str] = [
    "symbol",
    "name",
    "market",
    "sector",
    "themes",
    "avg_daily_value",
    "data_ready",
]

# pykrx returns Korean column names for daily OHLCV frames.
_PYKRX_COLUMN_MAP = {
    "open": "시가",
    "high": "고가",
    "low": "저가",
    "close": "종가",
    "volume": "거래량",
}


@dataclass(frozen=True)
class FetchSummary:
    symbols: tuple[str, ...]
    security_count: int
    bar_count: int
    skipped_bars: int
    out_dir: Path


def frame_to_bars(symbol: str, frame: Any) -> tuple[list[dict[str, Any]], int]:
    """Convert one pykrx daily OHLCV DataFrame into provider-schema bar rows.

    Bars with non-positive prices (KRX reports zeros on no-trade days) are
    skipped rather than written, because the CSV provider fails closed on them.
    Returns ``(bars, skipped_count)``.
    """
    bars: list[dict[str, Any]] = []
    skipped = 0
    for index, row in frame.iterrows():
        values = {name: float(row[column]) for name, column in _PYKRX_COLUMN_MAP.items()}
        if min(values["open"], values["high"], values["low"], values["close"]) <= 0:
            skipped += 1
            continue
        bars.append(
            {
                "symbol": symbol,
                "date": index.date().isoformat() if hasattr(index, "date") else str(index),
                "open": values["open"],
                "high": values["high"],
                "low": values["low"],
                "close": values["close"],
                "volume": int(values["volume"]),
            }
        )
    return bars, skipped


def build_security_rows(
    names: dict[str, str],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    market: str,
    sectors: dict[str, str],
) -> list[dict[str, Any]]:
    """Build securities.csv rows; avg_daily_value is the mean of close*volume."""
    rows: list[dict[str, Any]] = []
    for symbol in sorted(bars_by_symbol):
        bars = bars_by_symbol[symbol]
        if not bars:
            raise ProviderError(f"no usable daily bars fetched for {symbol}")
        traded_value = sum(bar["close"] * bar["volume"] for bar in bars) / len(bars)
        rows.append(
            {
                "symbol": symbol,
                "name": names.get(symbol, symbol),
                "market": market,
                "sector": sectors.get(symbol, "unknown"),
                "themes": "",
                "avg_daily_value": round(traded_value, 2),
                "data_ready": "true",
            }
        )
    return rows


def write_local_data(
    out_dir: Path,
    security_rows: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "securities.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_SECURITY_COLUMNS)
        writer.writeheader()
        writer.writerows(security_rows)
    with (out_dir / "ohlcv.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_OHLCV_COLUMNS)
        writer.writeheader()
        for symbol in sorted(bars_by_symbol):
            writer.writerows(bars_by_symbol[symbol])


def validate_output(out_dir: Path) -> tuple[int, int]:
    """Re-load the written files through the fail-closed local providers.

    Returns ``(security_count, bar_count)``; raises ``ProviderError`` if the
    output would not be consumable by ``DATA_MODE=local_historical``.
    """
    security_provider, market_data_provider = build_providers(
        DataMode.local_historical, data_dir=out_dir
    )
    securities = security_provider.get_securities()
    history = market_data_provider.get_price_history()
    market_data_provider.get_bars()  # exercises the indicator path (ma20/rsi)
    return len(securities), len(history)


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {raw!r}")


def _parse_sectors(raw: str) -> dict[str, str]:
    sectors: dict[str, str] = {}
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        if "=" not in token:
            raise argparse.ArgumentTypeError(
                f"--sectors entries must look like SYMBOL=sector, got {token!r}"
            )
        symbol, sector = token.split("=", 1)
        sectors[symbol.strip().upper()] = sector.strip().lower()
    return sectors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", required=True, help="comma-separated KRX tickers, e.g. 005930,000660")
    parser.add_argument("--start", required=True, type=_parse_date, help="start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=_parse_date, help="end date YYYY-MM-DD")
    parser.add_argument("--out-dir", required=True, help="directory for securities.csv / ohlcv.csv")
    parser.add_argument("--market", default="KR_STOCK", help="market label written to securities.csv")
    parser.add_argument(
        "--sectors",
        default={},
        type=_parse_sectors,
        help="optional SYMBOL=sector overrides, e.g. 005930=technology,000660=technology",
    )
    return parser.parse_args(argv)


def fetch_krx_local_data(
    symbols: list[str],
    start: date,
    end: date,
    out_dir: Path,
    *,
    market: str = "KR_STOCK",
    sectors: dict[str, str] | None = None,
) -> FetchSummary:
    try:
        from pykrx import stock
    except ImportError:
        raise SystemExit(
            "pykrx is required for this job: python -m pip install pykrx"
        )

    if start >= end:
        raise SystemExit("--start must be before --end")

    fromdate = start.strftime("%Y%m%d")
    todate = end.strftime("%Y%m%d")
    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    names: dict[str, str] = {}
    skipped_total = 0
    for symbol in symbols:
        frame = stock.get_market_ohlcv(fromdate, todate, symbol)
        bars, skipped = frame_to_bars(symbol, frame)
        skipped_total += skipped
        bars_by_symbol[symbol] = bars
        names[symbol] = str(stock.get_market_ticker_name(symbol) or symbol)

    security_rows = build_security_rows(
        names, bars_by_symbol, market=market, sectors=sectors or {}
    )
    write_local_data(out_dir, security_rows, bars_by_symbol)
    security_count, bar_count = validate_output(out_dir)
    return FetchSummary(
        symbols=tuple(sorted(bars_by_symbol)),
        security_count=security_count,
        bar_count=bar_count,
        skipped_bars=skipped_total,
        out_dir=out_dir,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if not symbols:
        raise SystemExit("--symbols must contain at least one ticker")
    summary = fetch_krx_local_data(
        symbols,
        args.start,
        args.end,
        Path(args.out_dir),
        market=args.market,
        sectors=args.sectors,
    )
    print(
        f"wrote {summary.bar_count} bars for {summary.security_count} symbol(s) "
        f"to {summary.out_dir} (skipped {summary.skipped_bars} no-trade bars); "
        f"validated via DATA_MODE=local_historical providers"
    )
    print(
        "next: set DATA_MODE=local_historical and "
        f"LOCAL_DATA_DIR={summary.out_dir} then run the harness or API"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
