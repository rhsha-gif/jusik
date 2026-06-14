from __future__ import annotations

from collections import Counter
from typing import Protocol

from quantpilot.packages.core.ledger.types import LedgerEntry, LedgerEventType


class LedgerStore(Protocol):
    def append(self, entry: LedgerEntry) -> LedgerEntry: ...

    def list(self) -> list[LedgerEntry]: ...

    def by_order_plan_id(self, order_plan_id: str) -> list[LedgerEntry]: ...

    def by_idempotency_key(self, idempotency_key: str) -> list[LedgerEntry]: ...

    def by_event_type(self, event_type: LedgerEventType) -> list[LedgerEntry]: ...

    def clear(self) -> None: ...


class InMemoryLedgerStore:
    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._by_dedupe_key: dict[str, LedgerEntry] = {}

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        existing = self._by_dedupe_key.get(entry.dedupe_key)
        if existing is not None:
            return existing

        sequenced = entry.model_copy(update={"sequence": len(self._entries) + 1})
        self._entries.append(sequenced)
        self._by_dedupe_key[sequenced.dedupe_key] = sequenced
        return sequenced

    def list(self) -> list[LedgerEntry]:
        return list(self._entries)

    def by_order_plan_id(self, order_plan_id: str) -> list[LedgerEntry]:
        return [entry for entry in self._entries if entry.order_plan_id == order_plan_id]

    def by_idempotency_key(self, idempotency_key: str) -> list[LedgerEntry]:
        return [entry for entry in self._entries if entry.idempotency_key == idempotency_key]

    def by_event_type(self, event_type: LedgerEventType) -> list[LedgerEntry]:
        return [entry for entry in self._entries if entry.event_type == event_type]

    def summary(self, *, policy_id: str | None = None) -> dict[str, object]:
        entries = [entry for entry in self._entries if policy_id is None or entry.policy_id == policy_id]
        event_counts = Counter(entry.event_type.value for entry in entries)
        return {
            "ledger_event_count": len(entries),
            "ledger_event_counts": dict(sorted(event_counts.items())),
            "ledger_sources": sorted({entry.source for entry in entries}),
            "ledger_entry_ids": [entry.ledger_entry_id for entry in entries],
        }

    def clear(self) -> None:
        self._entries.clear()
        self._by_dedupe_key.clear()
