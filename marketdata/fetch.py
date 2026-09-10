"""CLI entry point: fetch daily bars from a source and write CSV (+ chart).

Usage::

    python -m marketdata.fetch --source upbit --symbol KRW-BTC

Fetching completes fully in memory before any file is touched, so a failed
download can never corrupt an existing CSV.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from marketdata.sources.upbit import UpbitDailySource
from marketdata.types import BAR_FIELDS, Bar

# New sources register here; the CLI and chart need no other change.
SOURCES = {"upbit": UpbitDailySource}


def _validate(bars: list[Bar]) -> None:
    if not bars:
        raise ValueError("source returned zero bars; refusing to write an empty CSV")
    for bar in bars:
        if bar["high"] < bar["low"]:
            raise ValueError(f"bar {bar['date']}: high {bar['high']} < low {bar['low']}")
        for field in ("open", "high", "low", "close"):
            if bar[field] <= 0:
                raise ValueError(f"bar {bar['date']}: {field} is {bar[field]} (must be > 0)")


def _write_csv(bars: list[Bar], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=BAR_FIELDS)
        writer.writeheader()
        writer.writerows(bars)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m marketdata.fetch",
        description="Fetch full daily OHLCV history and render a standalone candle chart.",
    )
    parser.add_argument("--source", default="upbit", choices=sorted(SOURCES))
    parser.add_argument("--symbol", default="KRW-BTC")
    parser.add_argument("--out-dir", default="marketdata/data", type=Path)
    parser.add_argument("--chart-dir", default="marketdata/out", type=Path)
    parser.add_argument("--no-chart", action="store_true", help="write only the CSV")
    args = parser.parse_args(argv)

    source = SOURCES[args.source]()
    bars = source.fetch_daily(args.symbol)
    _validate(bars)

    csv_path = args.out_dir / f"{source.name}_{args.symbol}_1d.csv"
    _write_csv(bars, csv_path)

    print(
        f"{source.name} {args.symbol}: {len(bars)} bars, "
        f"{bars[0]['date']} ~ {bars[-1]['date']}, last close {bars[-1]['close']:,.0f}"
    )
    print(f"csv:   {csv_path}")

    if not args.no_chart:
        from marketdata.chart import render_chart

        chart_path = args.chart_dir / f"{source.name}_{args.symbol}_1d.html"
        chart_path.parent.mkdir(parents=True, exist_ok=True)
        chart_path.write_text(
            render_chart(bars, f"{args.symbol} 1D ({source.name})"), encoding="utf-8"
        )
        print(f"chart: {chart_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
