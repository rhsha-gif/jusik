"""EXP-015 · Does the ranking edge survive the eras the 10y sample never saw?

EXP-013/014 measured a large, stable-looking edge for allocating deposits by
trend rank instead of at random. Every one of those measurements lived inside
2016-2026, a stretch where momentum worked almost without pause. The obvious way
for that whole result to be wrong is regime dependence, and the two regimes most
likely to break it -- the 2000-2002 unwind and 2008 -- were simply absent.

This re-runs the chosen Level-4 configuration decade by decade on the full
history pulled by fetch_long.py.

    seed 5    names the person picks by hand
    book 10   total names the account may hold
    pool      approved candidates the system fills the rest from
    control   same pool, same seed, same dates, slots filled at random

On survivorship, which is severe here and must not be glossed:
    only 4 delisted names survive in the long file -- Yahoo no longer serves
    tickers that died decades ago. So the pre-2010 universe is close to
    "companies that made it", and ABSOLUTE returns from those eras are inflated
    and are not quoted as performance anywhere below.
    The paired difference is what this measures, and both arms draw from the
    same survivor-biased pool on the same dates, so the bias inflates both and
    largely cancels. That is the one number this data can honestly support.

Research only.
"""

from __future__ import annotations

import csv
import math
import random
import statistics
from datetime import date
from pathlib import Path

from rank_deposit import (
    COMMISSION_CAP_RATE,
    COMMISSION_MINIMUM,
    COMMISSION_PER_SHARE,
    DEPOSIT_TARGETS,
    INITIAL_CAPITAL,
    MONTHLY_DEPOSIT,
    SLIPPAGE_BPS,
    WARMUP_MONTHS,
    Series,
    build_rankings,
)

DATA_DIR = Path(__file__).resolve().parent / "data_long"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

TRIALS = 200
SEED = 20260727
SEED_NAMES = 5
BOOK = 10
POOLS = [50, 80]

ERAS: list[tuple[str, str, str]] = [
    ("1973-1982 stagflation", "1973-01", "1982-12"),
    ("1983-1992", "1983-01", "1992-12"),
    ("1993-2002 dot-com", "1993-01", "2002-12"),
    ("2000-2009 lost decade", "2000-01", "2009-12"),
    ("2003-2012 GFC", "2003-01", "2012-12"),
    ("2013-2022", "2013-01", "2022-12"),
    ("2016-2026 original", "2016-01", "2026-07"),
]


def load(symbol: str) -> Series:
    closes: list[float] = []
    opens: list[float] = []
    dates: list[date] = []
    with (DATA_DIR / f"{symbol}.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            dates.append(date.fromisoformat(row["date"]))
            opens.append(float(row["open"]))
            closes.append(float(row["close"]))
    series = Series(symbol=symbol, closes=closes, opens=opens)
    for index, day in enumerate(dates):
        key = f"{day.year:04d}-{day.month:02d}"
        series.month_end[key] = index
        series.month_first.setdefault(key, index)
    return series


def commission(shares: float, price: float) -> float:
    return min(max(COMMISSION_MINIMUM, COMMISSION_PER_SHARE * shares), shares * price * COMMISSION_CAP_RATE)


def simulate(
    series_map: dict[str, Series],
    window: list[str],
    rankings: dict[str, list[str]],
    pool: set[str],
    start: list[str],
    mode: str,
    rng: random.Random,
) -> float:
    shares: dict[str, float] = {}
    cash = INITIAL_CAPITAL
    deposited = INITIAL_CAPITAL

    def close_price(symbol: str, key: str) -> float | None:
        index = series_map[symbol].month_end.get(key)
        return series_map[symbol].closes[index] if index is not None else None

    def buy(symbol: str, key: str, amount: float) -> None:
        nonlocal cash
        index = series_map[symbol].month_first.get(key)
        if index is None:
            return
        price = series_map[symbol].opens[index]
        amount = min(amount, cash)
        if price <= 0 or amount <= 1.0:
            return
        price *= 1 + SLIPPAGE_BPS / 10_000
        fee = commission(amount / price, price)
        shares[symbol] = shares.get(symbol, 0.0) + (amount - fee) / price
        cash -= amount

    for symbol in start:
        buy(symbol, window[0], INITIAL_CAPITAL / len(start))

    for t in range(len(window) - 1):
        key, key_next = window[t], window[t + 1]
        cash += MONTHLY_DEPOSIT
        deposited += MONTHLY_DEPOSIT
        if mode in ("even", "underweight"):
            # Both of these stay inside the book -- they never reach into the pool,
            # so they are the arms the confirmed design already contains.
            if mode == "even":
                targets = list(shares)
            else:
                held = cash + sum(h * (close_price(s, key) or 0.0) for s, h in shares.items())
                per = held / max(1, len(shares))
                gaps = {s: per - shares[s] * (close_price(s, key) or 0.0) for s in shares}
                targets = [s for s, g in sorted(gaps.items(), key=lambda kv: -kv[1]) if g > 0][:DEPOSIT_TARGETS]
        else:
            eligible = [s for s in rankings.get(key, []) if s in pool]
            if mode == "random":
                eligible = list(eligible)
                rng.shuffle(eligible)
            room = BOOK - len(shares)
            targets = [s for s in eligible if s in shares or room > 0][:DEPOSIT_TARGETS]
        if not targets:
            targets = list(shares)
        for symbol in targets:
            buy(symbol, key_next, MONTHLY_DEPOSIT / len(targets))

    final = cash + sum(h * (close_price(s, window[-1]) or 0.0) for s, h in shares.items())
    return final / deposited


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest]
    series_map = {s: load(s) for s in symbols}
    all_keys = sorted({k for s in series_map.values() for k in s.month_end})

    print(f"loaded {len(symbols)} names   months {all_keys[0]} .. {all_keys[-1]}")
    print("building monthly rankings across the full history (cached once) ...")
    rankings = build_rankings(series_map, all_keys)
    print()

    rows: list[list[object]] = []
    print("=" * 110)
    print(f"EXP-015  ranking edge by era   seed={SEED_NAMES} book={BOOK}  {TRIALS} trials per cell, nothing sold")
    print("absolute multiples are survivorship-inflated before 2010; read the EDGE column")
    print("=" * 110)
    print(f"{'era':>24s}{'pool':>6s}{'names':>7s}{'ranked':>10s}{'random':>9s}"
          f"{'edge':>11s}{'median':>11s}{'t':>8s}{'win':>8s}{'verdict':>12s}")

    for label, start_key, end_key in ERAS:
        window = [k for k in all_keys if start_key <= k <= end_key]
        if len(window) < 60:
            continue
        warm = window[0]
        # a name is usable in this era only if it quotes for essentially all of it
        available = sorted(
            s for s in symbols
            if sum(1 for k in window if k in series_map[s].month_end) >= len(window) * 0.95
            and rankings.get(warm) is not None
        )
        print("-" * 110)
        for pool_size in POOLS:
            if len(available) < pool_size + 5:
                print(f"{label:>24s}{pool_size:>6d}{len(available):>7d}{'  -- too few names in this era':>60s}")
                continue
            rng_master = random.Random(SEED)
            gaps: list[float] = []
            ranked_out: list[float] = []
            random_out: list[float] = []
            for trial in range(TRIALS):
                pool = set(rng_master.sample(available, pool_size))
                start = rng_master.sample(sorted(pool), SEED_NAMES)
                a = simulate(series_map, window, rankings, pool, start, "rank", random.Random(SEED + trial))
                b = simulate(series_map, window, rankings, pool, start, "random", random.Random(SEED + trial))
                ranked_out.append(a)
                random_out.append(b)
                gaps.append(a - b)
            mean = statistics.fmean(gaps)
            se = statistics.pstdev(gaps) / math.sqrt(len(gaps))
            t = mean / se if se > 0 else 0.0
            win = sum(1 for g in gaps if g > 0) / len(gaps) * 100
            median = statistics.median(gaps)
            verdict = "pays" if (t > 2.47 and median > 0) else ("mean only" if t > 2.47 else "no value")
            print(
                f"{label:>24s}{pool_size:>6d}{len(available):>7d}{statistics.fmean(ranked_out):9.3f}x"
                f"{statistics.fmean(random_out):8.3f}x{mean:+10.4f}x{median:+10.4f}x{t:+8.2f}{win:7.1f}%{verdict:>12s}"
            )
            rows.append([label, pool_size, len(available), statistics.fmean(ranked_out),
                         statistics.fmean(random_out), mean, median, t, win])
    print("=" * 110)

    with (RESULTS_DIR / "era_test.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["era", "pool", "names_available", "ranked_mult", "random_mult", "mean_edge", "median_edge", "t", "win_pct"])
        writer.writerows(rows)

    # The ranking is regime-dependent. The arm actually written into the confirmed
    # design is buy_only -- feed whichever holding has fallen furthest behind. It
    # never reaches into the pool and it is contrarian rather than trend-following,
    # so there is no reason to assume it fails in the same decades. Measured, not assumed.
    print()
    print("=" * 110)
    print(f"EXP-015b  buy_only (feed the most underweight holding) vs even split -- the arm already in the design")
    print("book of 10 fixed names, no pool, nothing sold; contrarian rather than trend-following")
    print("=" * 110)
    print(f"{'era':>24s}{'names':>7s}{'buy_only':>11s}{'even':>9s}{'edge':>11s}{'median':>11s}{'t':>8s}{'win':>8s}{'verdict':>12s}")
    print("-" * 110)
    b_rows: list[list[object]] = []
    for label, start_key, end_key in ERAS:
        window = [k for k in all_keys if start_key <= k <= end_key]
        if len(window) < 60:
            continue
        available = sorted(
            s for s in symbols
            if sum(1 for k in window if k in series_map[s].month_end) >= len(window) * 0.95
        )
        if len(available) < BOOK + 5:
            continue
        rng_master = random.Random(SEED)
        gaps: list[float] = []
        a_out: list[float] = []
        b_out: list[float] = []
        for trial in range(TRIALS):
            start = rng_master.sample(available, BOOK)
            pool = set(start)
            a = simulate(series_map, window, rankings, pool, start, "underweight", random.Random(SEED + trial))
            b = simulate(series_map, window, rankings, pool, start, "even", random.Random(SEED + trial))
            a_out.append(a)
            b_out.append(b)
            gaps.append(a - b)
        mean = statistics.fmean(gaps)
        se = statistics.pstdev(gaps) / math.sqrt(len(gaps))
        t = mean / se if se > 0 else 0.0
        win = sum(1 for g in gaps if g > 0) / len(gaps) * 100
        median = statistics.median(gaps)
        verdict = "pays" if (t > 2.47 and median > 0) else ("mean only" if t > 2.47 else "no value")
        print(
            f"{label:>24s}{len(available):>7d}{statistics.fmean(a_out):10.3f}x{statistics.fmean(b_out):8.3f}x"
            f"{mean:+10.4f}x{median:+10.4f}x{t:+8.2f}{win:7.1f}%{verdict:>12s}"
        )
        b_rows.append([label, len(available), statistics.fmean(a_out), statistics.fmean(b_out), mean, median, t, win])
    print("=" * 110)

    with (RESULTS_DIR / "era_test_buyonly.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["era", "names_available", "buy_only_mult", "even_mult", "mean_edge", "median_edge", "t", "win_pct"])
        writer.writerows(b_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
