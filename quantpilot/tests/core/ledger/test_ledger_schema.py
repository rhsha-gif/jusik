from __future__ import annotations

import json

from quantpilot.packages.core.ledger.types import LedgerEntry, LedgerEventType, ReconciliationLedger


def test_ledger_event_type_contract_is_stable() -> None:
    assert {event_type.value for event_type in LedgerEventType} == {
        "order_intent",
        "submitted",
        "fill",
        "partial_fill",
        "cancel",
        "reject",
        "position_update",
    }


def test_ledger_entry_serializes_with_safe_source_and_data_mode() -> None:
    entry = LedgerEntry(
        event_type=LedgerEventType.order_intent,
        policy_id="pol_fixture",
        policy_version=1,
        order_plan_id="oplan_fixture",
        intent_id="intent_fixture",
        idempotency_key="idem_fixture",
        dedupe_key="order_intent:idem_fixture:intent_fixture",
        source="mock",
        data_mode="fixture",
        symbol="AAA",
        side="buy",
        quantity=10,
        price=105,
        notional=1050,
        metadata={"broker_mode": "mock"},
    )
    ledger = ReconciliationLedger(entries=[entry])

    dumped = ledger.model_dump(mode="json")

    assert dumped["entries"][0]["event_type"] == "order_intent"
    assert dumped["entries"][0]["source"] == "mock"
    assert dumped["entries"][0]["data_mode"] == "fixture"
    assert dumped["entries"][0]["idempotency_key"] == "idem_fixture"
    json.dumps(dumped)
