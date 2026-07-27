"""EXP-002 audit · the user's trend-rebalancing protocol v0.1, re-scored honestly.

The headline run (30 hand-picked names, one 6.8y window) flattered the protocol
twice over: every name in it survived to the present, and a single window cannot
show whether an edge is stable or a lucky stretch.

This re-scores the same rules against:
    universe   the point-in-time set including delisted names (fetch_pit.py)
    windows    the period cut into halves and thirds
    buckets    growth / defensive / cyclical subsets

Nothing about the protocol is changed. Only the conditions it is measured under.

Research only.
"""

from __future__ import annotations

import csv
import statistics
from datetime import date
from pathlib import Path

import protocol as P

PIT_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
BENCHMARKS = {"SPY", "QQQ"}


def load_pit(symbol: str) -> P.Series:
    dates: list[date] = []
    opens: list[float] = []
    closes: list[float] = []
    with (PIT_DIR / f"{symbol}.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            dates.append(date.fromisoformat(row["date"]))
            opens.append(float(row["open"]))
            closes.append(float(row["close"]))
    series = P.Series(symbol=symbol, dates=dates, opens=opens, closes=closes)
    for index, day in enumerate(dates):
        key = f"{day.year:04d}-{day.month:02d}"
        series.month_end_index[key] = index
        series.month_first_index.setdefault(key, index)
    return series


def score(series_map: dict[str, P.Series], keys: list[str], window: tuple[str, str] | None) -> tuple[float, float, float, float]:
    hold = P.simulate(series_map, keys, protocol=False, window=window)
    proto = P.simulate(series_map, keys, protocol=True, window=window)
    m_hold, m_proto = P.metrics(hold), P.metrics(proto)
    return (
        m_hold["cagr_aftertax"],
        m_proto["cagr_aftertax"],
        m_proto["cagr_aftertax"] - m_hold["cagr_aftertax"],
        m_proto["mdd"] - m_hold["mdd"],
    )


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((PIT_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest if r["symbol"] not in BENCHMARKS]
    series_map = {s: load_pit(s) for s in symbols}

    # SPY's month calendar is the master; intersecting all names collapses the
    # span to nothing once delisted symbols are present.
    spy = load_pit("SPY")
    keys = sorted(spy.month_end_index)

    rows: list[list[object]] = []
    print("=" * 96)
    print(f"EXP-002 AUDIT  protocol v0.1 re-scored   universe={len(symbols)} point-in-time names (delisted included)")
    print("=" * 96)
    print(f"{'window':>22s}{'hold CAGR':>13s}{'protocol':>12s}{'edge':>11s}{'MDD diff':>12s}{'verdict':>14s}")
    print("-" * 96)

    span = (keys[P.WARMUP_MONTHS], keys[-1])
    mid = keys[P.WARMUP_MONTHS + (len(keys) - P.WARMUP_MONTHS) // 2]
    third = (len(keys) - P.WARMUP_MONTHS) // 3
    t1 = keys[P.WARMUP_MONTHS + third]
    t2 = keys[P.WARMUP_MONTHS + 2 * third]

    windows: list[tuple[str, tuple[str, str] | None]] = [
        ("full", None),
        (f"H1 {span[0]}~{mid}", (span[0], mid)),
        (f"H2 {mid}~{span[1]}", (mid, span[1])),
        (f"T1 {span[0]}~{t1}", (span[0], t1)),
        (f"T2 {t1}~{t2}", (t1, t2)),
        (f"T3 {t2}~{span[1]}", (t2, span[1])),
    ]

    edges: list[float] = []
    for label, window in windows:
        h, p, edge, mdd = score(series_map, keys, window)
        verdict = "beats hold" if edge > 0 else "LOSES"
        if label.startswith(("H", "T")):
            edges.append(edge)
        print(f"{label:>22s}{h * 100:12.2f}%{p * 100:11.2f}%{edge * 100:+10.2f}%p{mdd * 100:+11.2f}%p{verdict:>14s}")
        rows.append([label, h, p, edge, mdd])

    print("-" * 96)
    wins = sum(1 for e in edges if e > 0)
    print(f"   sub-period edges: {', '.join(f'{e * 100:+.2f}%p' for e in edges)}")
    print(f"   positive in {wins}/{len(edges)} sub-periods   spread {(max(edges) - min(edges)) * 100:.2f}%p")
    print(f"   mean {statistics.fmean(edges) * 100:+.2f}%p   stdev {statistics.pstdev(edges) * 100:.2f}%p")
    print("=" * 96)

    with (RESULTS_DIR / "protocol_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["window", "hold_cagr_aftertax", "protocol_cagr_aftertax", "edge", "mdd_diff"])
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
