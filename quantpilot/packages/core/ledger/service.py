from __future__ import annotations

from typing import Any

from quantpilot.packages.core.ledger.store import InMemoryLedgerStore, LedgerStore
from quantpilot.packages.core.ledger.types import LedgerEntry, LedgerEventType
from quantpilot.packages.core.schemas import BrokerMode, BrokerOrder, Fill, OrderPlan, UserPolicy


class ReconciliationLedgerService:
    def __init__(self, store: LedgerStore | None = None) -> None:
        self.store = store or InMemoryLedgerStore()

    def record_order_intent(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        intent = order_plan.intent
        return self._append(
            event_type=LedgerEventType.order_intent,
            policy=policy,
            order_plan=order_plan,
            dedupe_suffix=intent.intent_id,
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            price=intent.limit_price,
            notional=intent.notional,
            metadata={
                "reason": intent.reason,
                "target_weight": intent.target_weight,
                "order_type": intent.order_type.value,
                **(metadata or {}),
            },
        )

    def record_submitted(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        broker_order: BrokerOrder,
    ) -> LedgerEntry:
        intent = order_plan.intent
        return self._append(
            event_type=LedgerEventType.submitted,
            policy=policy,
            order_plan=order_plan,
            dedupe_suffix=broker_order.broker_order_id,
            intent_id=intent.intent_id,
            broker_order_id=broker_order.broker_order_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            price=intent.limit_price,
            notional=intent.notional,
            source=broker_order.broker_mode,
            metadata={"broker_reference": broker_order.broker_reference},
        )

    def record_fill(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        broker_order: BrokerOrder,
        fill: Fill,
        partial: bool = False,
    ) -> LedgerEntry:
        event_type = LedgerEventType.partial_fill if partial else LedgerEventType.fill
        return self._append(
            event_type=event_type,
            policy=policy,
            order_plan=order_plan,
            dedupe_suffix=fill.fill_id,
            intent_id=order_plan.intent.intent_id,
            broker_order_id=broker_order.broker_order_id,
            fill_id=fill.fill_id,
            symbol=fill.symbol,
            side=order_plan.intent.side,
            quantity=fill.quantity,
            price=fill.price,
            notional=fill.notional,
            source=broker_order.broker_mode,
        )

    def record_position_update(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        broker_order: BrokerOrder,
        fills: list[Fill],
    ) -> LedgerEntry:
        quantity = round(sum(fill.quantity for fill in fills), 6)
        notional = round(sum(fill.notional for fill in fills), 2)
        price = round(notional / quantity, 6) if quantity else None
        return self._append(
            event_type=LedgerEventType.position_update,
            policy=policy,
            order_plan=order_plan,
            dedupe_suffix=broker_order.broker_order_id,
            intent_id=order_plan.intent.intent_id,
            broker_order_id=broker_order.broker_order_id,
            symbol=order_plan.intent.symbol,
            side=order_plan.intent.side,
            quantity=quantity,
            price=price,
            notional=notional,
            source=broker_order.broker_mode,
            metadata={"fill_ids": [fill.fill_id for fill in fills]},
        )

    def record_cancel(self, *, policy: UserPolicy, order_plan: OrderPlan, reason: str) -> LedgerEntry:
        return self._append(
            event_type=LedgerEventType.cancel,
            policy=policy,
            order_plan=order_plan,
            dedupe_suffix=order_plan.order_plan_id,
            intent_id=order_plan.intent.intent_id,
            symbol=order_plan.intent.symbol,
            side=order_plan.intent.side,
            quantity=order_plan.intent.quantity,
            price=order_plan.intent.limit_price,
            notional=order_plan.intent.notional,
            metadata={"reason": reason},
        )

    def record_reject(self, *, policy: UserPolicy, order_plan: OrderPlan, reason: str) -> LedgerEntry:
        return self._append(
            event_type=LedgerEventType.reject,
            policy=policy,
            order_plan=order_plan,
            dedupe_suffix=order_plan.order_plan_id,
            intent_id=order_plan.intent.intent_id,
            symbol=order_plan.intent.symbol,
            side=order_plan.intent.side,
            quantity=order_plan.intent.quantity,
            price=order_plan.intent.limit_price,
            notional=order_plan.intent.notional,
            metadata={"reason": reason},
        )

    def record_simulator_event(
        self,
        *,
        event_type: LedgerEventType,
        policy_id: str,
        policy_version: int,
        order_plan_id: str,
        idempotency_key: str,
        source: BrokerMode | str,
        symbol: str | None = None,
        side: str | None = None,
        quantity: float | None = None,
        price: float | None = None,
        notional: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        normalized_source, data_mode = self._normalize_source(source)
        related_id = str((metadata or {}).get("fill_id") or (metadata or {}).get("broker_order_id") or order_plan_id)
        return self.store.append(
            LedgerEntry(
                event_type=event_type,
                policy_id=policy_id,
                policy_version=policy_version,
                order_plan_id=order_plan_id,
                idempotency_key=idempotency_key,
                dedupe_key=f"simulator:{event_type.value}:{idempotency_key}:{related_id}",
                source=normalized_source,
                data_mode=data_mode,
                symbol=symbol,
                side=side,  # type: ignore[arg-type]
                quantity=quantity,
                price=price,
                notional=notional,
                metadata=metadata or {},
            )
        )

    def list(self) -> list[LedgerEntry]:
        return self.store.list()

    def by_order_plan_id(self, order_plan_id: str) -> list[LedgerEntry]:
        return self.store.by_order_plan_id(order_plan_id)

    def by_idempotency_key(self, idempotency_key: str) -> list[LedgerEntry]:
        return self.store.by_idempotency_key(idempotency_key)

    def by_event_type(self, event_type: LedgerEventType) -> list[LedgerEntry]:
        return self.store.by_event_type(event_type)

    def summary(self, *, policy_id: str | None = None) -> dict[str, object]:
        summary = self.store.summary(policy_id=policy_id) if hasattr(self.store, "summary") else {}
        return {
            "event_count": summary.get("ledger_event_count", len(self.store.list())),
            "event_counts": summary.get("ledger_event_counts", {}),
            "sources": summary.get("ledger_sources", []),
            **summary,
        }

    def _append(
        self,
        *,
        event_type: LedgerEventType,
        policy: UserPolicy,
        order_plan: OrderPlan,
        dedupe_suffix: str,
        intent_id: str | None = None,
        broker_order_id: str | None = None,
        fill_id: str | None = None,
        symbol: str | None = None,
        side: str | None = None,
        quantity: float | None = None,
        price: float | None = None,
        notional: float | None = None,
        source: BrokerMode | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        normalized_source, data_mode = self._normalize_source(source or policy.broker)
        return self.store.append(
            LedgerEntry(
                event_type=event_type,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                order_plan_id=order_plan.order_plan_id,
                intent_id=intent_id,
                broker_order_id=broker_order_id,
                fill_id=fill_id,
                idempotency_key=order_plan.idempotency_key,
                dedupe_key=f"{event_type.value}:{order_plan.idempotency_key}:{dedupe_suffix}",
                source=normalized_source,
                data_mode=data_mode,
                symbol=symbol,
                side=side,  # type: ignore[arg-type]
                quantity=quantity,
                price=price,
                notional=notional,
                order_status=order_plan.status.value,
                metadata=metadata or {},
            )
        )

    def _normalize_source(self, source: BrokerMode | str) -> tuple[str, str]:
        value = source.value if isinstance(source, BrokerMode) else str(source)
        if value == BrokerMode.mock.value:
            return "mock", "fixture"
        if value == BrokerMode.paper.value:
            return "paper", "paper_trading"
        raise ValueError("reconciliation ledger supports mock/paper sources only")
