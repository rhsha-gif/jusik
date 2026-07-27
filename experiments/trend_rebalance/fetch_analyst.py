"""Download analyst upgrade/downgrade history (stdlib only, Yahoo crumb flow).

This is the first NON-PRICE dataset in the project. Institutional research says
sell decisions should follow "the thesis, evidence, or risk limits" rather than
price, and an analyst downgrade is the cheapest observable proxy for
"fundamentals deteriorated" that is available for free with dates attached.

Yahoo now requires a cookie + crumb for quoteSummary, so the flow is:
    GET fc.yahoo.com                     -> cookies
    GET /v1/test/getcrumb                -> crumb
    GET /v10/finance/quoteSummary/...    -> payload

Known limits, recorded rather than hidden:
    * delisted names return HTTP errors -> survivorship bias unchanged
    * coverage scales with market cap (NVDA 982 events, GE 174)
    * whether Yahoo's stored history is exactly what was visible at the time
      cannot be verified; rating changes are public on announcement, so it is
      close to point-in-time but not guaranteed

Research only.
"""

from __future__ import annotations

import csv
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
OUT = DATA_DIR / "_analyst.csv"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
BENCHMARKS = {"SPY", "QQQ"}


def make_opener() -> tuple[urllib.request.OpenerDirector, str]:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        opener.open(urllib.request.Request("https://fc.yahoo.com", headers={"User-Agent": USER_AGENT}), timeout=20)
    except Exception:
        pass  # the 404 still sets the cookies we need
    request = urllib.request.Request(
        "https://query1.finance.yahoo.com/v1/test/getcrumb", headers={"User-Agent": USER_AGENT}
    )
    crumb = opener.open(request, timeout=20).read().decode()
    if not crumb:
        raise RuntimeError("empty crumb - Yahoo auth flow changed")
    return opener, crumb


def fetch(opener: urllib.request.OpenerDirector, crumb: str, symbol: str) -> list[dict[str, object]]:
    url = (
        f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
        f"?modules=upgradeDowngradeHistory&crumb={urllib.parse.quote(crumb)}"
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    payload = json.loads(opener.open(request, timeout=25).read().decode())
    result = payload["quoteSummary"]["result"]
    if not result:
        return []
    history = result[0].get("upgradeDowngradeHistory", {}).get("history", [])

    rows: list[dict[str, object]] = []
    for entry in history:
        stamp = entry.get("epochGradeDate")
        if not stamp:
            continue
        rows.append(
            {
                "symbol": symbol,
                "date": datetime.fromtimestamp(int(stamp), tz=timezone.utc).date().isoformat(),
                "firm": str(entry.get("firm", "")).replace(",", " "),
                "from_grade": str(entry.get("fromGrade", "")),
                "to_grade": str(entry.get("toGrade", "")),
                "action": str(entry.get("action", "")),
            }
        )
    return rows


def main() -> int:
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [row["symbol"] for row in manifest if row["symbol"] not in BENCHMARKS]
    opener, crumb = make_opener()
    print(f"crumb ok, fetching {len(symbols)} symbols")

    all_rows: list[dict[str, object]] = []
    failed: list[str] = []
    for index, symbol in enumerate(symbols, 1):
        try:
            rows = fetch(opener, crumb, symbol)
        except (urllib.error.URLError, KeyError, IndexError, ValueError) as exc:
            failed.append(f"{symbol}({type(exc).__name__})")
            continue
        all_rows.extend(rows)
        if index % 25 == 0:
            print(f"  {index}/{len(symbols)}  events so far {len(all_rows)}")
        time.sleep(0.35)

    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "date", "firm", "from_grade", "to_grade", "action"])
        writer.writeheader()
        writer.writerows(all_rows)

    covered = len({str(r["symbol"]) for r in all_rows})
    downs = sum(1 for r in all_rows if r["action"] == "down")
    ups = sum(1 for r in all_rows if r["action"] == "up")
    print(f"\nsymbols covered {covered}/{len(symbols)}   events {len(all_rows)}   down {downs}   up {ups}")
    if failed:
        print(f"failed {len(failed)}: {', '.join(failed[:20])}{' ...' if len(failed) > 20 else ''}")
    print(f"written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
