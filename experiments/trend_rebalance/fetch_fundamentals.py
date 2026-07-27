"""Point-in-time annual fundamentals from SEC XBRL (stdlib only, free).

Needed for the one selection signal left standing: gross profitability, which
Novy-Marx (2013) documents as a long-horizon cross-sectional predictor, plus the
two discipline terms (asset growth, share issuance) that enter QMJ-style scores
with a negative sign.

Why this signal and not the others tested: it is a STATE, not an event. EXP-009
and EXP-017 both measured events that follow a price decline, and both collapsed
once the control was matched on that decline. A balance-sheet ratio has no such
confound -- it is not triggered by anything the price did.

Two things decide whether the data is honest:

    filed, not period end.
        Every XBRL fact carries the date it was filed. FY2023 numbers are not
        public until early 2024, so scoring them from January 2024 forward is
        the only non-look-ahead choice. This file keeps `filed` on every row and
        the analysis must key off it.

    tag drift.
        US-GAAP tags change under the companies' feet -- Apple reports
        `Revenues` through FY2018 and `RevenueFromContractWithCustomer...`
        after ASC 606. A single-tag pull silently truncates history, so each
        concept has a fallback chain and every tag that hits is merged.

Research only.
"""

from __future__ import annotations

import csv
import gzip
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
LONG_DIR = Path(__file__).resolve().parent / "data_long"
OUT = DATA_DIR / "_fundamentals.csv"

HEADERS = {
    "User-Agent": "QuantPilot Research research@example.com",
    "Accept-Encoding": "gzip, deflate",
}
TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
THROTTLE = 0.12

CONCEPTS: dict[str, list[str]] = {
    "assets": ["Assets"],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "cogs": [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
        "CostOfServices",
    ],
    "equity": ["StockholdersEquity"],
    "shares": ["CommonStockSharesOutstanding", "CommonStockSharesIssued"],
}
FIELDS = ["symbol", "concept", "tag", "fiscal_end", "filed", "value"]


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode())


def universe() -> list[str]:
    names: set[str] = set()
    for directory in (DATA_DIR, LONG_DIR):
        manifest = directory / "_manifest.csv"
        if manifest.exists():
            with manifest.open(encoding="utf-8") as handle:
                names.update(row["symbol"] for row in csv.DictReader(handle))
    return sorted(names)


def annual_facts(cik: str, tag: str) -> list[dict[str, object]]:
    """Annual (10-K, FY) observations only, deduplicated by fiscal period end.

    A restated figure appears twice with different `filed` dates; the EARLIEST
    filing is kept, because that is what an investor could actually have seen.
    """
    try:
        payload = fetch_json(CONCEPT.format(cik=cik, tag=tag))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return []
    units = payload.get("units", {}).get("USD") or payload.get("units", {}).get("shares") or []
    best: dict[str, dict[str, object]] = {}
    for unit in units:
        if unit.get("form") != "10-K" or unit.get("fp") != "FY":
            continue
        end, filed, val = unit.get("end"), unit.get("filed"), unit.get("val")
        if not end or not filed or val is None:
            continue
        current = best.get(str(end))
        if current is None or str(filed) < str(current["filed"]):
            best[str(end)] = {"fiscal_end": end, "filed": filed, "value": val}
    return list(best.values())


def main() -> int:
    symbols = universe()
    if not symbols:
        print("no manifest found")
        return 1

    print("loading ticker -> CIK map ...")
    mapping = fetch_json(TICKER_MAP)
    cik_of = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in mapping.values()}
    resolved = {s: cik_of[s] for s in symbols if s in cik_of}
    print(f"universe {len(symbols)}   CIK resolved {len(resolved)}   "
          f"unmatched {len(symbols) - len(resolved)} (delisted tickers are expected to miss)")

    rows: list[dict[str, object]] = []
    for index, (symbol, cik) in enumerate(sorted(resolved.items()), 1):
        for concept, tags in CONCEPTS.items():
            for tag in tags:
                facts = annual_facts(cik, tag)
                time.sleep(THROTTLE)
                if not facts:
                    continue
                for fact in facts:
                    rows.append({
                        "symbol": symbol, "concept": concept, "tag": tag,
                        "fiscal_end": fact["fiscal_end"], "filed": fact["filed"],
                        "value": fact["value"],
                    })
                # keep going through the chain: tags change mid-history, and a
                # later tag often covers years the first one does not
        if index % 25 == 0:
            print(f"  {index}/{len(resolved)}   rows {len(rows):,}")

    if not rows:
        print("nothing collected")
        return 1

    rows.sort(key=lambda r: (str(r["symbol"]), str(r["concept"]), str(r["fiscal_end"])))
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    covered = {str(r["symbol"]) for r in rows}
    years = sorted({str(r["fiscal_end"])[:4] for r in rows})
    by_concept: dict[str, int] = {}
    for row in rows:
        by_concept[str(row["concept"])] = by_concept.get(str(row["concept"]), 0) + 1
    print(f"\nrows {len(rows):,}   symbols {len(covered)}   fiscal years {years[0]} .. {years[-1]}")
    print("   " + "   ".join(f"{k} {v:,}" for k, v in sorted(by_concept.items())))
    print(f"written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
