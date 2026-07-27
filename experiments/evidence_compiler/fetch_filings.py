"""Point-in-time filing store from SEC EDGAR (stdlib only, free).

Stores 10-K / 10-Q / 8-K documents keyed by ACCEPTANCE time -- the moment the
filing became public -- because that, not the fiscal period and not even the
filing date, is what an investor could have acted on. Everything downstream
(LLM extraction, scoring, the prospective test) keys off this timestamp.

The document text is flattened to plain text with paragraph breaks. Exact
whitespace is NOT preserved; the evidence validator therefore normalises
whitespace on both sides before checking that a quoted span exists. That check
is the contract: an LLM quote that cannot be located verbatim in this store is
rejected regardless of how plausible it sounds.

Usage:
    python fetch_filings.py NVDA MSFT --forms 10-K,10-Q --limit 4

Research only.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FILINGS_DIR = BASE_DIR / "filings"
INDEX = FILINGS_DIR / "_index.csv"

HEADERS = {
    "User-Agent": "QuantPilot Research research@example.com",
    "Accept-Encoding": "gzip, deflate",
}
TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
THROTTLE = 0.25
BLOCK_TAGS = {"p", "div", "tr", "br", "li", "table", "h1", "h2", "h3", "h4"}
INDEX_FIELDS = ["symbol", "form", "accession", "filing_date", "acceptance", "report_period", "text_path", "chars"]


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in joined.splitlines()]
        out: list[str] = []
        for line in lines:
            if line:
                out.append(line)
            elif out and out[-1] != "":
                out.append("")
        return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--forms", default="10-K,10-Q,8-K")
    parser.add_argument("--limit", type=int, default=4, help="filings per symbol")
    args = parser.parse_args()
    wanted_forms = {f.strip().upper() for f in args.forms.split(",")}

    FILINGS_DIR.mkdir(parents=True, exist_ok=True)
    mapping = json.loads(fetch(TICKER_MAP).decode())
    cik_of = {v["ticker"].upper(): int(v["cik_str"]) for v in mapping.values()}

    existing: dict[str, dict[str, str]] = {}
    if INDEX.exists():
        with INDEX.open(encoding="utf-8") as handle:
            existing = {row["accession"]: row for row in csv.DictReader(handle)}

    for symbol in [s.upper() for s in args.symbols]:
        cik = cik_of.get(symbol)
        if cik is None:
            print(f"{symbol}: no CIK, skipped")
            continue
        subs = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").decode())
        recent = subs["filings"]["recent"]
        taken = 0
        for i in range(len(recent["form"])):
            form = recent["form"][i]
            if form not in wanted_forms or taken >= args.limit:
                continue
            accession = recent["accessionNumber"][i]
            if accession in existing:
                taken += 1
                continue
            primary = recent["primaryDocument"][i]
            if not primary:
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{primary}"
            try:
                raw = fetch(url)
            except Exception as exc:
                print(f"  {symbol} {form} {accession}: fetch failed ({type(exc).__name__})")
                continue
            extractor = TextExtractor()
            extractor.feed(raw.decode("utf-8", errors="replace"))
            text = extractor.text()

            sub_dir = FILINGS_DIR / symbol
            sub_dir.mkdir(exist_ok=True)
            text_path = sub_dir / f"{accession}.txt"
            text_path.write_text(text, encoding="utf-8")
            existing[accession] = {
                "symbol": symbol, "form": form, "accession": accession,
                "filing_date": recent["filingDate"][i],
                "acceptance": recent["acceptanceDateTime"][i],
                "report_period": recent.get("reportDate", [""] * len(recent["form"]))[i],
                "text_path": str(text_path.relative_to(BASE_DIR)),
                "chars": str(len(text)),
            }
            taken += 1
            print(f"  {symbol} {form:5s} {accession}  accepted {recent['acceptanceDateTime'][i]}  {len(text):,} chars")
            time.sleep(THROTTLE)

    rows = sorted(existing.values(), key=lambda r: (r["symbol"], r["acceptance"]))
    with INDEX.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nindex: {len(rows)} filings -> {INDEX}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
