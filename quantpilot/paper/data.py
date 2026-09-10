"""Paper-only minute reads and public ranking fallback, with explicit observation provenance."""

from __future__ import annotations
from datetime import datetime, timedelta
from dataclasses import dataclass
import json
import math
import re
import threading
import time
import urllib.request
import urllib.parse
from quantpilot.paper.http import open_request

from quantpilot.paper.calendar import KST
from quantpilot.paper.config import aware
from quantpilot.packages.core.kis_paper import KisPaperClient, _assert_business_success


class DataUnavailable(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, interval=1.05, clock=time.monotonic, sleep=time.sleep):
        self.interval = interval
        self.clock = clock
        self.sleep = sleep
        self.last = -float("inf")
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            self.sleep(max(0, self.interval - (self.clock() - self.last)))
            self.last = self.clock()


class LimitedTransport:
    """One shared budget for authentication, data, orders and reconciliation."""

    def __init__(self, transport, limiter=None):
        self.transport = transport
        self.limiter = limiter or RateLimiter()

    def request_json(self, *args, **kwargs):
        self.limiter.wait()
        return self.transport.request_json(*args, **kwargs)


def symbol_code(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{6}", value):
        raise DataUnavailable("invalid_symbol")
    return value


def parse_minutes(symbol, rows, now):
    from quantpilot.paper.strategy import Bar

    day = aware(now).astimezone(KST).date()
    result = []
    seen = set()
    if not isinstance(rows, list) or len(rows) > 1000:
        raise DataUnavailable("minute_rows_invalid")
    for row in rows:
        if not isinstance(row, dict):
            raise DataUnavailable("minute_row_invalid")
        stamp = str(row.get("stck_bsop_date", "")) + str(row.get("stck_cntg_hour", ""))
        try:
            at = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=KST)
            if at.date() != day or at.second != 0 or at > now:
                raise ValueError()
            if at + timedelta(minutes=1) > now:
                continue  # forming candle, not a closed-bar input
            if at in seen:
                raise ValueError()
            seen.add(at)
            bar = Bar(
                symbol_code(symbol),
                at,
                float(row["stck_oprc"]),
                float(row["stck_hgpr"]),
                float(row["stck_lwpr"]),
                float(row["stck_prpr"]),
                float(row["cntg_vol"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise DataUnavailable("minute_payload_invalid") from None
        result.append(bar)
    return sorted(result, key=lambda b: b.start)


class PaperMarket:
    def __init__(self, client: KisPaperClient, quote_provider, public_fetch=None):
        self.client = client
        self.quote_provider = quote_provider
        self.public_fetch = public_fetch or self._public_fetch

    def minutes(self, symbol, now):
        response = self.client._authenticated_get(
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            "FHKST03010200",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol_code(symbol),
                "FID_INPUT_HOUR_1": now.astimezone(KST).strftime("%H%M%S"),
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_ETC_CLS_CODE": "",
            },
        )
        _assert_business_success(response, "paper_minutes")
        return parse_minutes(symbol, response.payload.get("output2"), now)

    def quotes(self, symbols):
        snap = self.quote_provider.get_quotes(symbols)
        if not snap.data_quality.usable:
            raise DataUnavailable("paper_quotes_unavailable")
        return snap.quotes

    @staticmethod
    def _public_fetch(market):
        # Public read only, never an alternate execution or authenticated real-data host.
        url = f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize=100"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "QuantPilot-paper/1.0",
                "Accept": "application/json",
            },
        )
        with open_request(req, timeout=10) as r:
            if urllib.parse.urlsplit(r.url).hostname != "m.stock.naver.com":
                raise DataUnavailable("ranking_redirect")
            raw = r.read(1_000_001)
            if len(raw) > 1_000_000:
                raise DataUnavailable("ranking_oversized")
            return json.loads(raw)

    def candidates(self, now, limit):
        try:
            response = self.client._authenticated_get(
                "/uapi/domestic-stock/v1/quotations/volume-rank",
                "FHPST01710000",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_COND_SCR_DIV_CODE": "20171",
                    "FID_INPUT_ISCD": "0000",
                    "FID_DIV_CLS_CODE": "0",
                    "FID_BLNG_CLS_CODE": "3",
                    "FID_TRGT_CLS_CODE": "111111111",
                    "FID_TRGT_EXLS_CLS_CODE": "1111111111",
                    "FID_INPUT_PRICE_1": "",
                    "FID_INPUT_PRICE_2": "",
                    "FID_VOL_CNT": "",
                    "FID_INPUT_DATE_1": "",
                },
            )
            _assert_business_success(response, "paper_ranking")
            rows = response.payload.get("output")
            if not isinstance(rows, list):
                raise DataUnavailable("rank_payload")
            codes = [symbol_code(r["mksc_shrn_iscd"]) for r in rows]
            if codes:
                return list(dict.fromkeys(codes))[:limit], "kis_paper_ranking"
        except Exception:
            pass
        candidates = []
        for market in ("KOSPI", "KOSDAQ"):
            payload = self.public_fetch(market)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("stocks"), list
            ):
                raise DataUnavailable("public_ranking_payload")
            for row in payload["stocks"]:
                # Market-cap universe is explicitly limited; do not claim whole-market coverage.
                code = symbol_code(row.get("itemCode"))
                raw = str(row.get("accumulatedTradingValue", "")).replace(",", "")
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if math.isfinite(value) and value > 0:
                    candidates.append((value, code))
        if not candidates:
            raise DataUnavailable("universe_unavailable")
        return (
            list(dict.fromkeys(code for _, code in sorted(candidates, reverse=True)))[
                :limit
            ],
            "public_largecap_liquidity_subset",
        )
