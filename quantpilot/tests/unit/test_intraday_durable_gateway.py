from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace
from types import SimpleNamespace
import pytest

from quantpilot.paper.store import Store
from quantpilot.paper.broker import KisGateway
from quantpilot.paper.calendar import Session
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.kis_paper import (
    KisBalanceResult,
    KisBalanceSummary,
    KisBalancePosition,
    KisDailyOrderFill,
    KisDailyOrdersResult,
)
from quantpilot.tests.unit.test_paper_submission_coordinator import FakePaperClient

NOW = datetime(2026, 9, 10, 1, 0, 2, tzinfo=timezone.utc)


class Client(FakePaperClient):
    def get_cancelable_orders(self):
        return SimpleNamespace(rows=[])

    def __init__(self):
        super().__init__()
        self.filled = False
        self.buying_power = replace(
            self.buying_power,
            orderable_cash=Decimal("10000000"),
            no_receivable_buy_amount=Decimal("10000000"),
            no_receivable_buy_quantity=142,
        )

    def get_balance(self, **kw):
        positions = (
            (
                KisBalancePosition(
                    "005930",
                    "fixture",
                    1,
                    1,
                    Decimal("70000"),
                    Decimal("70000"),
                    Decimal("70000"),
                    Decimal("70000"),
                ),
            )
            if self.filled
            else ()
        )
        return KisBalanceResult(
            positions,
            KisBalanceSummary(
                Decimal("10000000"),
                Decimal("10000000"),
                Decimal("70000") if self.filled else Decimal(0),
                Decimal("70000") if self.filled else Decimal(0),
                Decimal("10070000") if self.filled else Decimal("10000000"),
                Decimal(0),
            ),
            1,
        )

    def get_daily_orders_and_fills(self, *args, **kw):
        return KisDailyOrdersResult(
            (
                KisDailyOrderFill(
                    "0000012345",
                    "",
                    "91234",
                    "20260910",
                    "100001",
                    "005930",
                    "fixture",
                    "buy",
                    1,
                    Decimal("70000"),
                    1 if self.filled else 0,
                    Decimal("70000") if self.filled else Decimal(0),
                    0 if self.filled else 1,
                    0,
                    False,
                    0,
                    Decimal("70000") if self.filled else Decimal(0),
                ),
            ),
            1,
        )


def test_new_profile_uses_existing_durable_journal_and_reconciles_once(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    s.control("start")
    s.put("weights", {"trend_pullback": 0.6})
    client = Client()
    now = [NOW]
    calendar = SimpleNamespace(
        session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5)),
        current_open_session_date=lambda at: at.date(),
    )
    g = KisGateway(s, client, calendar, lambda: now[0])
    g.begin()
    g.reconcile(NOW)
    signal = SimpleNamespace(
        symbol="005930",
        strategy_id="trend_pullback",
        stop=69000.0,
        target=72000.0,
        version="1",
        entry_atr14=1000.0,
    )
    s.reserve(
        order_id="paper-test",
        signal=signal,
        quantity=1,
        price=70000,
        side="buy",
        now=NOW,
        policy_version=1,
        reason="fixture",
    )
    q = Quote(symbol="005930", last=70000, bid=69900, ask=70000, as_of=NOW)
    g.submit(s.orders()[0], q, NOW)
    assert client.order_calls == 1
    assert s.get("outside_cash_reserve") == 5_000_000
    dispatch = g.kernel.load_paper_order_dispatch("paper-test")
    assert dispatch.attempt_count == 1 and dispatch.status == "accepted"
    assert not s.positions()  # acceptance is not a fill
    client.filled = True
    now[0] += timedelta(seconds=1)
    g.reconcile(now[0])
    cash = s.get("cash")
    g.reconcile(now[0])
    assert (
        len(s.positions()) == 1
        and s.positions()[0]["quantity"] == 1
        and s.get("cash") == cash
    )
    assert client.order_calls == 1
    assert s.get("cash") < 5_000_000 and s.get("cash") > 4_900_000
    g.end()
    g.close()
    s.close()


def test_missing_account_attribution_blocks_new_profile(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    client = Client()
    client.filled = True
    cal = SimpleNamespace(current_open_session_date=lambda at: at.date())
    g = KisGateway(s, client, cal, lambda: NOW)
    g.begin()
    assert g.reconcile(NOW) is False
    assert client.order_calls == 0
    g.end()
    g.close()
    s.close()
