"""Self-audit · were the significance figures in EXP-013..016 computed against the
right sample size?

Every t reported so far divided by sqrt(200), where 200 was the number of random
portfolios. Those portfolios are not independent draws. They trade the same
148-234 companies over the same calendar, so they share almost all of their
market path; only the name selection differs. Treating them as 200 independent
observations shrinks the standard error by roughly an order of magnitude and
inflates every t accordingly.

The independent unit is the PERIOD, not the portfolio. Two decades of market
history are close to independent; two portfolios inside one decade are not.

This recomputes the headline numbers with the era as the unit, and separately
with only the non-overlapping eras, since several of the seven windows share
years with each other and cannot both count as evidence.

Reads the CSVs the experiments already wrote. Changes no conclusions by itself --
it only reports what the evidence actually supports.
"""

from __future__ import annotations

import csv
import math
import statistics
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# 2000-2009 overlaps 1993-2002 and 2003-2012; 2016-2026 overlaps 2013-2022.
NON_OVERLAPPING = {
    "1973-1982 stagflation",
    "1983-1992",
    "1993-2002 dot-com",
    "2003-2012 GFC",
    "2013-2022",
}


def t_stat(values: list[float]) -> tuple[float, float, float]:
    n = len(values)
    if n < 2:
        return 0.0, 0.0, 0.0
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / math.sqrt(n)   # sample stdev, not population
    return mean, se, mean / se if se > 0 else 0.0


def main() -> int:
    rows = list(csv.DictReader((RESULTS_DIR / "blend.csv").open(encoding="utf-8")))
    shares = sorted({float(r["rank_share"]) for r in rows})

    print("=" * 96)
    print("SELF-AUDIT  significance recomputed with the ERA as the independent unit")
    print("=" * 96)
    print("as reported earlier, the unit was the portfolio (n=150..200) -- these are not")
    print("independent draws, so those t values were inflated. Below uses one number per era.")
    print()
    print(f"{'rank share':>12s}{'eras':>7s}{'mean':>11s}{'SE':>10s}{'t':>9s}{'pos':>8s}{'worst':>11s}{'verdict':>14s}")
    print("-" * 96)

    for label, keep in (("ALL SEVEN ERAS", None), ("NON-OVERLAPPING ONLY", NON_OVERLAPPING)):
        print(f"{label}")
        for share in shares:
            values = [
                float(r["median_edge"]) for r in rows
                if float(r["rank_share"]) == share and (keep is None or r["era"] in keep)
            ]
            mean, se, t = t_stat(values)
            positive = sum(1 for v in values if v > 0)
            verdict = "survives" if t > 2.47 else ("weak" if t > 1.0 else "not supported")
            print(
                f"{share * 100:11.0f}%{len(values):>7d}{mean:+10.4f}x{se:9.4f}{t:+9.2f}"
                f"{positive:>5d}/{len(values):<2d}{min(values):+10.4f}x{verdict:>14s}"
            )
        print()

    print("=" * 96)
    print("what changes: the earlier claim that a 25% rank sleeve is the best compromise")
    print("rested on a per-portfolio t. Re-run against the required 2.47 SE bar for 90 trials.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
