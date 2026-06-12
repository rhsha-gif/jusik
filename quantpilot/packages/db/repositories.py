from __future__ import annotations

from typing import Callable, Generic, Protocol, TypeVar

from quantpilot.packages.core.schemas import (
    AuditLogEvent,
    BrokerOrder,
    Fill,
    OperationReport,
    OrderPlan,
    PortfolioPlan,
    Signal,
    StrategyRecipe,
    UserPolicy,
)


T = TypeVar("T")


class RepositoryInterface(Protocol[T]):
    def add(self, item: T) -> T: ...

    def get(self, item_id: str) -> T | None: ...

    def require(self, item_id: str) -> T: ...

    def list(self) -> list[T]: ...

    def update(self, item: T) -> T: ...

    def clear(self) -> None: ...


class RepositoryError(RuntimeError):
    pass


class InMemoryRepository(Generic[T]):
    def __init__(self, id_getter: Callable[[T], str]) -> None:
        self._id_getter = id_getter
        self._items: dict[str, T] = {}

    def add(self, item: T) -> T:
        item_id = self._id_getter(item)
        if item_id in self._items:
            raise RepositoryError(f"duplicate id: {item_id}")
        self._items[item_id] = item
        return item

    def get(self, item_id: str) -> T | None:
        return self._items.get(item_id)

    def require(self, item_id: str) -> T:
        item = self.get(item_id)
        if item is None:
            raise RepositoryError(f"missing item: {item_id}")
        return item

    def list(self) -> list[T]:
        return list(self._items.values())

    def update(self, item: T) -> T:
        item_id = self._id_getter(item)
        if item_id not in self._items:
            raise RepositoryError(f"cannot update missing item: {item_id}")
        self._items[item_id] = item
        return item

    def clear(self) -> None:
        self._items.clear()


class RepositoryRegistry:
    def __init__(self) -> None:
        self.policies = InMemoryRepository[UserPolicy](lambda item: item.policy_id)
        self.strategies = InMemoryRepository[StrategyRecipe](lambda item: item.strategy_id)
        self.signals = InMemoryRepository[Signal](lambda item: item.signal_id)
        self.portfolio_plans = InMemoryRepository[PortfolioPlan](lambda item: item.plan_id)
        self.order_plans = InMemoryRepository[OrderPlan](lambda item: item.order_plan_id)
        self.broker_orders = InMemoryRepository[BrokerOrder](lambda item: item.broker_order_id)
        self.fills = InMemoryRepository[Fill](lambda item: item.fill_id)
        self.audit_logs = InMemoryRepository[AuditLogEvent](lambda item: item.event_id)
        self.operation_reports = InMemoryRepository[OperationReport](lambda item: item.report_id)

    def clear(self) -> None:
        self.policies.clear()
        self.strategies.clear()
        self.signals.clear()
        self.portfolio_plans.clear()
        self.order_plans.clear()
        self.broker_orders.clear()
        self.fills.clear()
        self.audit_logs.clear()
        self.operation_reports.clear()
