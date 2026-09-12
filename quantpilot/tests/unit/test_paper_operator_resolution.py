"""Operator-gated resolution of ambiguous broker states (2026-09-12 audit F1/F2).

The trader never re-sends a cancellation or closes an outcome_unknown row on its own.
An operator may release a cancel claim after fresh daily evidence shows the original
order still working with no cancel row, and may close an unknown row as rejected after
a fresh query-only pass found nothing and the account holding matches the ledger.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quantpilot.paper.broker import KisGateway
from quantpilot.paper.calendar import Session
from quantpilot.paper.operator import release_cancel_claim, resolve_unknown_dispatch
from quantpilot.paper.store import Store
from quantpilot.packages.core.kis_paper import KisDailyOrdersResult
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.tests.unit.test_intraday_durable_gateway import Client, NOW


@pytest.fixture
def trial(tmp_path):
    """Same shape as test_paper_daily_cancel.trial, on an explicit paper profile."""
    store = Store(tmp_path / "experiment.sqlite3")
    store.configure({"data_mode": "paper_trading"}, 1)
    store.control("start")
    store.put("weights", {"trend_pullback": 0.6})
    client = Client()
    client.order_outcome = replace(
        client.order_outcome, quantity=1, krx_forwarding_order_org_number="00950"
    )
    client.get_cancelable_orders = lambda: (_ for _ in ()).throw(
        AssertionError("paper native cancelable inquiry must not be called")
    )
    calls = []

    def cancel(**kwargs):
        assert store.get("cancel_claim:trial") is True
        calls.append(kwargs)
        return SimpleNamespace(message_code="40630000")

    client.cancel_paper_remaining_order = cancel
    calendar = SimpleNamespace(
        session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5)),
        current_open_session_date=lambda at: at.date(),
    )
    gateway = KisGateway(store, client, calendar, lambda: NOW)
    gateway.begin()
    assert gateway.reconcile(NOW)
    store.reserve(
        order_id="trial",
        signal=SimpleNamespace(
            symbol="005930",
            strategy_id="trend_pullback",
            version="1",
            stop=69000.0,
            target=72000.0,
            entry_atr14=1000.0,
        ),
        quantity=1,
        price=70000,
        side="buy",
        now=NOW,
        policy_version=store.policy.version,
        reason="fixture",
    )
    gateway.submit(
        store.orders()[0],
        Quote(symbol="005930", last=70000, bid=69900, ask=70000, as_of=NOW),
        NOW,
    )
    assert gateway.reconcile(NOW)
    yield store, client, gateway, calls
    gateway.end()
    gateway.close()
    store.close()


def _timeout_cancel(store, client, gateway, calls):
    def timeout(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("fixture")

    client.cancel_paper_remaining_order = timeout
    with pytest.raises(TimeoutError):
        gateway.cancel(store.orders()[0], NOW)
    assert store.get("cancel_claim:trial") is True and len(calls) == 1


def test_operator_release_lets_trader_resend_cancel_once_after_fresh_evidence(trial):
    store, client, gateway, calls = trial
    _timeout_cancel(store, client, gateway, calls)
    # The trader never re-sends on its own.
    gateway.cancel(store.orders()[0], NOW)
    assert len(calls) == 1
    with pytest.raises(ValueError, match="paused"):
        release_cancel_claim(store, client, "trial", NOW, kernel=gateway.kernel)
    store.control("pause")
    result = release_cancel_claim(store, client, "trial", NOW, kernel=gateway.kernel)
    assert result["status"] == "cancel_claim_released"
    assert result["remaining_quantity"] == 1
    assert store.get("cancel_claim:trial") is None
    kinds = [r["kind"] for r in store.db.execute("SELECT kind FROM audit")]
    assert "cancel_claim_released" in kinds
    gateway.reconcile(NOW)
    assert store.orders()[0]["state"] == "accepted"
    with pytest.raises(TimeoutError):
        gateway.cancel(store.orders()[0], NOW)
    assert len(calls) == 2 and store.get("cancel_claim:trial") is True


@pytest.mark.parametrize(
    "change",
    [
        {
            "remaining_quantity": 0,
            "total_filled_quantity": 1,
            "average_fill_price": Decimal("70000"),
            "total_filled_amount": Decimal("70000"),
        },
        {"order_number": "9999999999"},
    ],
)
def test_operator_release_refuses_without_a_working_original_row(trial, change):
    store, client, gateway, calls = trial
    _timeout_cancel(store, client, gateway, calls)
    store.control("pause")
    row = replace(client.get_daily_orders_and_fills().rows[0], **change)
    client.get_daily_orders_and_fills = lambda *a, **k: KisDailyOrdersResult((row,), 1)
    with pytest.raises(ValueError, match="daily_evidence_unmatched"):
        release_cancel_claim(store, client, "trial", NOW, kernel=gateway.kernel)
    assert store.get("cancel_claim:trial") is True


def test_operator_release_refuses_when_a_cancel_child_row_exists(trial):
    store, client, gateway, calls = trial
    _timeout_cancel(store, client, gateway, calls)
    store.control("pause")
    row = client.get_daily_orders_and_fills().rows[0]
    child = replace(
        row,
        order_number="0000012346",
        original_order_number=row.order_number,
        remaining_quantity=0,
        rejected_quantity=1,
    )
    client.get_daily_orders_and_fills = lambda *a, **k: KisDailyOrdersResult(
        (row, child), 1
    )
    with pytest.raises(ValueError, match="cancel_request_already_recorded"):
        release_cancel_claim(store, client, "trial", NOW, kernel=gateway.kernel)
    assert store.get("cancel_claim:trial") is True


def test_reconcile_and_submit_do_not_repeat_broker_inquiries(trial):
    store, client, gateway, calls = trial
    # The fixture submitted one buy: exactly one buying-power inquiry, not two.
    assert client.buying_power_calls == 1
    balance_calls = []
    original = client.get_balance

    def counting(**kw):
        balance_calls.append(kw)
        return original(**kw)

    client.get_balance = counting
    gateway.reconcile(NOW)
    assert len(balance_calls) == 1


@pytest.fixture
def unknown(tmp_path):
    """A buy whose POST raised after the durable claim: outcome_unknown, no broker row."""
    store = Store(tmp_path / "experiment.sqlite3")
    store.configure({"data_mode": "paper_trading"}, 1)
    store.control("start")
    store.put("weights", {"trend_pullback": 0.6})
    client = Client()
    client.order_outcome = TimeoutError("fixture")
    client.get_daily_orders_and_fills = lambda *a, **k: KisDailyOrdersResult((), 1)
    calendar = SimpleNamespace(
        session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5)),
        current_open_session_date=lambda at: at.date(),
    )
    gateway = KisGateway(store, client, calendar, lambda: NOW)
    gateway.begin()
    assert gateway.reconcile(NOW)
    store.reserve(
        order_id="unknown",
        signal=SimpleNamespace(
            symbol="005930",
            strategy_id="trend_pullback",
            version="1",
            stop=69000.0,
            target=72000.0,
            entry_atr14=1000.0,
        ),
        quantity=1,
        price=70000,
        side="buy",
        now=NOW,
        policy_version=store.policy.version,
        reason="fixture",
    )
    with pytest.raises(Exception):
        gateway.submit(
            store.orders()[0],
            Quote(symbol="005930", last=70000, bid=69900, ask=70000, as_of=NOW),
            NOW,
        )
    assert not gateway.reconcile(NOW)
    assert store.orders()[0]["state"] == "outcome_unknown"
    assert gateway.kernel.load_paper_order_dispatch("unknown").status == "outcome_unknown"
    yield store, client, gateway
    gateway.end()
    gateway.close()
    store.close()


LATER = NOW + timedelta(minutes=11)
REASON = "operator checked HTS: no order exists"


def test_operator_resolution_closes_unknown_after_window_and_zero_evidence(unknown):
    store, client, gateway = unknown
    store.control("pause")
    with pytest.raises(ValueError, match="resolution_window_not_elapsed"):
        resolve_unknown_dispatch(
            store, client, "unknown", REASON, NOW + timedelta(minutes=5), kernel=gateway.kernel
        )
    with pytest.raises(ValueError, match="resolution_reason_required"):
        resolve_unknown_dispatch(store, client, "unknown", "short", LATER, kernel=gateway.kernel)
    result = resolve_unknown_dispatch(
        store, client, "unknown", REASON, LATER, kernel=gateway.kernel
    )
    assert result["status"] == "resolved_as_rejected"
    dispatch = gateway.kernel.load_paper_order_dispatch("unknown")
    assert dispatch.status == "rejected"
    assert dispatch.reconciliation_status == "reconciled"
    assert dispatch.last_error_code == "operator_resolved_no_broker_evidence"
    assert store.orders()[0]["state"] == "rejected"
    kinds = [r["kind"] for r in store.db.execute("SELECT kind FROM audit")]
    assert "unknown_dispatch_resolved" in kinds
    assert gateway.reconcile(LATER)
    with pytest.raises(ValueError, match="order_not_outcome_unknown"):
        resolve_unknown_dispatch(
            store, client, "unknown", REASON, LATER, kernel=gateway.kernel
        )


def test_operator_resolution_refuses_when_broker_shows_the_order(unknown):
    store, client, gateway = unknown
    store.control("pause")
    working = Client()
    working.order_calls = 1
    client.get_daily_orders_and_fills = working.get_daily_orders_and_fills
    with pytest.raises(ValueError, match="broker_evidence_found"):
        resolve_unknown_dispatch(
            store, client, "unknown", REASON, LATER, kernel=gateway.kernel
        )
    # The fresh pass recorded the broker row instead: the order is now accepted, not lost.
    assert gateway.kernel.load_paper_order_dispatch("unknown").status == "accepted"


def test_operator_resolution_refuses_on_account_holding_mismatch(unknown):
    store, client, gateway = unknown
    store.control("pause")
    client.filled = True  # the account holds one share the ledger cannot attribute
    with pytest.raises(ValueError, match="account_holding_mismatch"):
        resolve_unknown_dispatch(
            store, client, "unknown", REASON, LATER, kernel=gateway.kernel
        )
    assert gateway.kernel.load_paper_order_dispatch("unknown").status == "outcome_unknown"


def test_operator_resolution_requires_paused_profile(unknown):
    store, client, gateway = unknown
    with pytest.raises(ValueError, match="operator_requires_paused_profile"):
        resolve_unknown_dispatch(
            store, client, "unknown", REASON, LATER, kernel=gateway.kernel
        )


def test_operator_resolution_opens_and_closes_its_own_kernel_on_the_cli_path(unknown):
    # Security-gate finding SG-20260912-01: the close flag must survive the evidence scan.
    store, client, gateway = unknown
    store.control("pause")
    result = resolve_unknown_dispatch(store, client, "unknown", REASON, LATER)
    assert result["status"] == "resolved_as_rejected"
    assert gateway.kernel.load_paper_order_dispatch("unknown").status == "rejected"
    assert store.orders()[0]["state"] == "rejected"
    # A second CLI-owned kernel is opened and closed again without error.
    with pytest.raises(ValueError, match="order_not_outcome_unknown"):
        resolve_unknown_dispatch(store, client, "unknown", REASON, LATER)


def test_operator_resolution_refuses_when_an_unowned_identical_row_exists_outside_the_window(unknown):
    # Independent review P1: the reconciler only matches an unknown row inside a short
    # window around the claim. A same-day identical row nobody owns is still this order.
    store, client, gateway = unknown
    store.control("pause")
    late = Client()
    late.order_calls = 1
    row = late.get_daily_orders_and_fills().rows[0]
    assert row.order_time == "100001"  # the claim happened at 10:00:02 KST
    outside = replace(row, order_time="100500")
    client.get_daily_orders_and_fills = lambda *a, **k: KisDailyOrdersResult((outside,), 1)
    with pytest.raises(ValueError, match="broker_evidence_ambiguous"):
        resolve_unknown_dispatch(
            store, client, "unknown", REASON, LATER, kernel=gateway.kernel
        )
    assert gateway.kernel.load_paper_order_dispatch("unknown").status == "outcome_unknown"
    assert store.orders()[0]["state"] == "outcome_unknown"


def test_reconcile_refreshes_the_balance_only_after_a_fill_was_applied(trial):
    store, client, gateway, calls = trial
    balance_calls = []
    original = client.get_balance

    def counting(**kw):
        balance_calls.append(kw)
        return original(**kw)

    client.get_balance = counting
    client.filled = True
    assert gateway.reconcile(NOW)
    assert store.positions()[0]["quantity"] == 1
    assert len(balance_calls) == 2  # reconciler snapshot + refresh after the fill
    assert gateway.reconcile(NOW)
    assert len(balance_calls) == 3  # steady state: one inquiry per pass


def test_gateway_refusal_on_order_post_is_a_counted_definitive_rejection(tmp_path):
    from quantpilot.packages.core.execution.paper_submission import (
        PaperSubmissionRejected,
    )
    from quantpilot.packages.core.kis_paper import KisPaperGatewayRejected, safe_failure

    store = Store(tmp_path / "experiment.sqlite3")
    store.configure({"data_mode": "paper_trading"}, 1)
    store.control("start")
    store.put("weights", {"trend_pullback": 0.6})
    client = Client()
    client.order_outcome = KisPaperGatewayRejected(
        "KIS paper gateway refused the request with HTTP status 500 (code=EGW00201)",
        code="EGW00201",
    )
    calendar = SimpleNamespace(
        session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5)),
        current_open_session_date=lambda at: at.date(),
    )
    gateway = KisGateway(store, client, calendar, lambda: NOW)
    gateway.begin()
    assert gateway.reconcile(NOW)
    store.reserve(
        order_id="throttled",
        signal=SimpleNamespace(
            symbol="005930",
            strategy_id="trend_pullback",
            version="1",
            stop=69000.0,
            target=72000.0,
            entry_atr14=1000.0,
        ),
        quantity=1,
        price=70000,
        side="buy",
        now=NOW,
        policy_version=store.policy.version,
        reason="fixture",
    )
    with pytest.raises(PaperSubmissionRejected) as info:
        gateway.submit(
            store.orders()[0],
            Quote(symbol="005930", last=70000, bid=69900, ask=70000, as_of=NOW),
            NOW,
        )
    assert safe_failure(info.value, "submit")["broker_code"] == "EGW00201"
    dispatch = gateway.kernel.load_paper_order_dispatch("throttled")
    assert dispatch.status == "rejected"
    assert dispatch.last_error_code == "broker_business_rejected"
    assert client.order_calls == 1
    client.get_daily_orders_and_fills = lambda *a, **k: KisDailyOrdersResult((), 1)
    assert gateway.reconcile(NOW)
    assert store.orders()[0]["state"] == "rejected" and not store.orders(True)
    gateway.end()
    gateway.close()
    store.close()
