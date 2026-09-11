"""Read-only KIS paper KRX tick/book stream; no account or order subscriptions."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import math

from quantpilot.paper.calendar import KST
from quantpilot.paper.config import aware
from quantpilot.paper.data import symbol_code
from quantpilot.packages.core.kis_paper import (
    KIS_PAPER_BASE_URL,
    KIS_WS_APPROVAL_ENDPOINT,
    KisPaperConfigurationError,
    StrictUrllibKisPaperTransport,
)

PAPER_WS = "ws://ops.koreainvestment.com:31000"
TICK_TR, QUOTE_TR = "H0STCNT0", "H0STASP0"


def parse_frame(raw, received_at, symbols):
    """Reject unrecognized/encrypted account frames before payload interpretation."""
    aware(received_at)
    if not isinstance(raw, str) or len(raw) > 1_048_576:
        raise ValueError("invalid_market_frame")
    parts = raw.split("|", 3)
    if len(parts) != 4 or parts[0] != "0" or parts[1] not in {TICK_TR, QUOTE_TR}:
        raise ValueError("non_market_frame")
    count = int(parts[2])
    width = 46 if parts[1] == TICK_TR else 59
    fields = parts[3].split("^")
    if not 1 <= count <= 100 or len(fields) != count * width:
        raise ValueError("market_frame_shape")
    events = []
    for i in range(count):
        row = fields[i * width : (i + 1) * width]
        symbol = symbol_code(row[0])
        if symbol not in symbols:
            raise ValueError("unsubscribed_symbol")
        day = received_at.astimezone(KST).date()
        occurred = datetime.strptime(
            day.isoformat() + " " + row[1], "%Y-%m-%d %H%M%S"
        ).replace(tzinfo=KST)
        if parts[1] == TICK_TR and row[33] != day.strftime("%Y%m%d"):
            raise ValueError("tick_date_mismatch")
        if not 0 <= (received_at - occurred).total_seconds() < 30:
            raise ValueError("market_event_delayed")
        event = {
            "id": sha256(
                (day.isoformat() + "|" + parts[1] + "|" + "^".join(row)).encode()
            ).hexdigest(),
            "kind": "tick" if parts[1] == TICK_TR else "quote",
            "symbol": symbol,
            "event_at": occurred.astimezone(timezone.utc).isoformat(),
            "received_at": received_at.astimezone(timezone.utc).isoformat(),
            "source": "kis_paper_stream",
        }
        if parts[1] == TICK_TR:
            event.update(price=float(row[2]), quantity=float(row[12]))
            if any(
                not math.isfinite(event[k]) or event[k] <= 0
                for k in ("price", "quantity")
            ):
                raise ValueError("invalid_tick")
        else:
            event.update(
                ask=float(row[3]),
                bid=float(row[13]),
                ask_size=float(row[23]),
                bid_size=float(row[33]),
            )
            if (
                any(
                    not math.isfinite(event[k]) or event[k] <= 0 for k in ("ask", "bid")
                )
                or event["bid"] > event["ask"]
            ):
                raise ValueError("invalid_book")
            if any(
                not math.isfinite(event[k]) or event[k] < 0
                for k in ("ask_size", "bid_size")
            ):
                raise ValueError("invalid_book_size")
        events.append(event)
    return events


class PaperApprovalTransport(StrictUrllibKisPaperTransport):
    """Allow only the fixed paper WebSocket authentication exchange."""

    def request_json(self, method, url, **kwargs):
        if (method, url) != ("POST", KIS_PAPER_BASE_URL + KIS_WS_APPROVAL_ENDPOINT):
            raise KisPaperConfigurationError(
                "paper stream authentication endpoint blocked"
            )
        return super().request_json(method, url, **kwargs)


def request_approval(config, transport):
    if config.base_url != KIS_PAPER_BASE_URL:
        raise ValueError("paper_origin_required")
    result = transport.request_json(
        "POST",
        KIS_PAPER_BASE_URL + KIS_WS_APPROVAL_ENDPOINT,
        headers={"content-type": "application/json"},
        params=None,
        body={
            "grant_type": "client_credentials",
            "appkey": config.app_key,
            "secretkey": config.app_secret,
        },
        timeout_seconds=10,
    )
    key = result.payload.get("approval_key")
    if (
        result.status_code != 200
        or not isinstance(key, str)
        or not 1 <= len(key) <= 2048
    ):
        raise ValueError("stream_approval_unavailable")
    return key


class PaperStream:
    def __init__(self, approval, symbols, *, connect=None):
        if not isinstance(approval, str) or not approval:
            raise ValueError("stream_approval_required")
        if not symbols or len(symbols) > 20 or len(set(symbols)) != len(symbols):
            raise ValueError("stream_symbol_limit")
        self._approval = approval
        self.symbols = tuple(symbol_code(s) for s in symbols)
        self._connect = connect

    def consume(self, on_event, clock, stop, symbols_provider=None):
        connect = self._connect
        if connect is None:
            from websockets.sync.client import connect
        # No URL override, proxy, production fallback, account channel or order operation.
        with connect(
            PAPER_WS, proxy=None, open_timeout=10, close_timeout=5, max_size=1_048_576
        ) as ws:
            observed_symbols = set(self.symbols)
            for symbol in self.symbols:
                for tr in (TICK_TR, QUOTE_TR):
                    ws.send(
                        json.dumps(
                            {
                                "header": {
                                    "approval_key": self._approval,
                                    "custtype": "P",
                                    "tr_type": "1",
                                    "content-type": "utf-8",
                                },
                                "body": {"input": {"tr_id": tr, "tr_key": symbol}},
                            }
                        )
                    )
            while not stop():
                if symbols_provider:
                    desired = tuple(symbol_code(s) for s in symbols_provider())
                    if (
                        not desired
                        or len(set(desired)) != len(desired)
                        or len(desired) > 20
                    ):
                        raise ValueError("stream_symbol_limit")
                    for action, codes in (
                        ("2", set(self.symbols) - set(desired)),
                        ("1", set(desired) - set(self.symbols)),
                    ):
                        for symbol in sorted(codes):
                            for tr in (TICK_TR, QUOTE_TR):
                                ws.send(
                                    json.dumps(
                                        {
                                            "header": {
                                                "approval_key": self._approval,
                                                "custtype": "P",
                                                "tr_type": action,
                                                "content-type": "utf-8",
                                            },
                                            "body": {
                                                "input": {"tr_id": tr, "tr_key": symbol}
                                            },
                                        }
                                    )
                                )
                    self.symbols = desired
                    observed_symbols.update(desired)
                try:
                    raw = ws.recv(timeout=1)
                except TimeoutError:
                    continue
                received = clock()
                if isinstance(raw, str) and raw.startswith("{"):
                    message = json.loads(raw)
                    tr = message.get("header", {}).get("tr_id")
                    if tr == "PINGPONG":
                        ws.pong(raw.encode())
                    elif (
                        tr not in {TICK_TR, QUOTE_TR}
                        or message.get("body", {}).get("rt_cd") != "0"
                    ):
                        raise ValueError("stream_subscription_rejected")
                    continue
                for event in parse_frame(raw, received, observed_symbols):
                    on_event(event)
