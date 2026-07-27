"""Insider open-market purchases from SEC Form 4 (stdlib only, free bulk data).

EXP-009 rejected analyst downgrades because they turned out to be a lagged
shadow of the price -- the stock had already fallen 3.73% before the downgrade
and did essentially nothing after. Insider buying is the natural counter-test:
executives buying their own stock with their own money, often INTO a decline,
is the one widely documented non-price signal that cannot be a reaction to the
price in the same way.

SEC publishes every Form 3/4/5 as quarterly structured TSV archives, free and
without authentication:
    https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets

Three tables are joined per quarter:
    SUBMISSION       ticker, filing date, document type
    NONDERIV_TRANS   transaction code, date, shares, price per share
    REPORTINGOWNER   whether the filer is an officer or a director

Only TRANS_CODE 'P' is kept -- an open-market purchase. Everything else on a
Form 4 is noise for this purpose: 'A' is a grant, 'M' an option exercise, 'F'
shares withheld for tax. Those happen on a schedule and carry no opinion.

Requests are throttled and carry the declared User-Agent SEC requires; without
it every endpoint returns 403.

Research only.
"""

from __future__ import annotations

import csv
import io
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
LONG_DIR = Path(__file__).resolve().parent / "data_long"
OUT = DATA_DIR / "_insider.csv"

BASE = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets"
HEADERS = {
    "User-Agent": "QuantPilot Research research@example.com",
    "Accept-Encoding": "gzip, deflate",
}
FIRST_YEAR, LAST_YEAR = 2015, 2026
PURCHASE_CODE = "P"
FIELDS = ["symbol", "trans_date", "filing_date", "owner_cik", "relationship", "shares", "price", "value"]


def universe() -> set[str]:
    names: set[str] = set()
    for directory in (DATA_DIR, LONG_DIR):
        manifest = directory / "_manifest.csv"
        if manifest.exists():
            with manifest.open(encoding="utf-8") as handle:
                names.update(row["symbol"] for row in csv.DictReader(handle))
    return names


MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def iso_date(raw: str) -> str:
    """SEC writes dates as DD-MON-YYYY ('24-JUL-2015'); some rows carry a time
    suffix. Truncating the string silently produces '24-JUL-201', so parse it."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    parts = raw.split("-")
    if len(parts) >= 3 and parts[1].upper()[:3] in MONTHS:
        day, month, year = parts[0], MONTHS[parts[1].upper()[:3]], parts[2][:4]
        if year.isdigit() and day.isdigit():
            return f"{int(year):04d}-{month:02d}-{int(day):02d}"
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    return ""


def read_table(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    with archive.open(name) as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace")
        return list(csv.DictReader(text, delimiter="\t"))


def relationship_of(row: dict[str, str]) -> str | None:
    """Form 4 encodes the relationship as a fixed-order flag string, e.g. '0|1|0|0'
    for director/officer/ten-percent-owner/other. Field layout has varied, so this
    reads whichever representation the quarter uses and keeps only insiders whose
    opinion is informative -- officers and directors, not passive 10% holders."""
    raw = (row.get("RPTOWNER_RELATIONSHIP") or "").strip()
    if not raw:
        return None
    flags = raw.split("|")
    if len(flags) >= 2:
        director = flags[0].strip() in ("1", "true", "True")
        officer = flags[1].strip() in ("1", "true", "True")
        if officer and director:
            return "officer_director"
        if officer:
            return "officer"
        if director:
            return "director"
        return None
    lowered = raw.lower()
    if "officer" in lowered and "director" in lowered:
        return "officer_director"
    if "officer" in lowered:
        return "officer"
    if "director" in lowered:
        return "director"
    return None


def quarter_rows(year: int, quarter: int, keep: set[str]) -> list[dict[str, object]]:
    url = f"{BASE}/{year}q{quarter}_form345.zip"
    request = urllib.request.Request(url, headers=HEADERS)
    payload = urllib.request.urlopen(request, timeout=120).read()
    archive = zipfile.ZipFile(io.BytesIO(payload))

    submissions = {
        row["ACCESSION_NUMBER"]: row
        for row in read_table(archive, "SUBMISSION.tsv")
        if row.get("DOCUMENT_TYPE") == "4"
        and (row.get("ISSUERTRADINGSYMBOL") or "").strip().upper() in keep
    }
    if not submissions:
        return []

    owners: dict[str, list[dict[str, str]]] = {}
    for row in read_table(archive, "REPORTINGOWNER.tsv"):
        accession = row["ACCESSION_NUMBER"]
        if accession in submissions:
            owners.setdefault(accession, []).append(row)

    out: list[dict[str, object]] = []
    for row in read_table(archive, "NONDERIV_TRANS.tsv"):
        if row.get("TRANS_CODE") != PURCHASE_CODE:
            continue
        accession = row["ACCESSION_NUMBER"]
        submission = submissions.get(accession)
        if submission is None:
            continue
        try:
            shares = float(row.get("TRANS_SHARES") or 0)
            price = float(row.get("TRANS_PRICEPERSHARE") or 0)
        except ValueError:
            continue
        if shares <= 0 or price <= 0:
            continue
        for owner in owners.get(accession, []):
            role = relationship_of(owner)
            if role is None:
                continue
            if not iso_date(row.get("TRANS_DATE", "")):
                continue
            out.append({
                "symbol": submission["ISSUERTRADINGSYMBOL"].strip().upper(),
                "trans_date": iso_date(row.get("TRANS_DATE", "")),
                "filing_date": iso_date(submission.get("FILING_DATE", "")),
                "owner_cik": owner.get("RPTOWNERCIK", ""),
                "relationship": role,
                "shares": round(shares, 2),
                "price": round(price, 4),
                "value": round(shares * price, 2),
            })
    return out


def main() -> int:
    keep = universe()
    if not keep:
        print("no manifest found -- run fetch_pit.py or fetch_long.py first")
        return 1
    print(f"universe {len(keep)} symbols; pulling {FIRST_YEAR}q1 .. {LAST_YEAR}q4")

    rows: list[dict[str, object]] = []
    missing: list[str] = []
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        for quarter in (1, 2, 3, 4):
            label = f"{year}q{quarter}"
            try:
                got = quarter_rows(year, quarter, keep)
            except urllib.error.HTTPError as exc:
                missing.append(f"{label}({exc.code})")
                continue
            except (urllib.error.URLError, zipfile.BadZipFile, KeyError) as exc:
                missing.append(f"{label}({type(exc).__name__})")
                continue
            rows.extend(got)
            print(f"  {label}  purchases {len(got):5d}   running {len(rows):6d}")
            time.sleep(0.3)

    if not rows:
        print("nothing collected")
        return 1

    rows.sort(key=lambda r: (str(r["symbol"]), str(r["trans_date"])))
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    symbols = {str(r["symbol"]) for r in rows}
    total = sum(float(str(r["value"])) for r in rows)
    print(f"\nrows {len(rows):,}   symbols {len(symbols)}/{len(keep)}   total value ${total/1e6:,.1f}M")
    print(f"date range {rows[0]['trans_date']} .. {max(str(r['trans_date']) for r in rows)}")
    if missing:
        print(f"quarters unavailable ({len(missing)}): {', '.join(missing)}")
    print(f"written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
