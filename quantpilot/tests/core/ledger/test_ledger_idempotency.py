from __future__ import annotations

from quantpilot.packages.core.ledger.service import ReconciliationLedgerService
from quantpilot.packages.core.ledger.store import InMemoryLedgerStore
from quantpilot.packages.core.ledger.types import LedgerEntry, LedgerEventType


def _entry(*, event_type: LedgerEventType = LedgerEventType.order_intent) -> LedgerEntry:
    return LedgerEntry(
        event_type=event_type,
        policy_id="pol_fixture",
        policy_version=1,
        order_plan_id="oplan_fixture",
        intent_id="intent_fixture",
        idempotency_key="idem_fixture",
        dedupe_key=f"{event_type.value}:idem_fixture:oplan_fixture",
        source="mock",
        data_mode="fixture",
    )


def test_append_is_idempotent_for_duplicate_event_dedupe_key() -> None:
    store = InMemoryLedgerStore()
    first = store.append(_entry())
    duplicate = store.append(_entry())

    assert duplicate.ledger_entry_id == first.ledger_entry_id
    assert store.list() == [first]


def test_same_order_idempotency_key_can_record_multiple_lifecycle_events() -> None:
    store = InMemoryLedgerStore()
    store.append(_entry(event_type=LedgerEventType.order_intent))
    store.append(_entry(event_type=LedgerEventType.submitted))
    store.append(_entry(event_type=LedgerEventType.fill))

    entries = store.by_idempotency_key("idem_fixture")

    assert [entry.event_type for entry in entries] == [
        LedgerEventType.order_intent,
        LedgerEventType.submitted,
        LedgerEventType.fill,
    ]


def test_simulator_adapter_records_fixture_safe_event() -> None:
    service = ReconciliationLedgerService(InMemoryLedgerStore())

    entry = service.record_simulator_event(
        event_type=LedgerEventType.fill,
        policy_id="pol_fixture",
        policy_version=1,
        order_plan_id="oplan_fixture",
        idempotency_key="idem_fixture",
        source="mock",
        symbol="AAA",
        quantity=5,
        price=105,
        notional=525,
    )

    assert entry.source == "mock"
    assert entry.data_mode == "fixture"
    assert service.summary()["event_counts"]["fill"] == 1
