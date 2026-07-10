from __future__ import annotations

from copy import deepcopy
from typing import Callable, Generic, Protocol, TypeVar

from pydantic import BaseModel

from quantpilot.packages.core.backtest.schemas import BacktestResult
from quantpilot.packages.core.schemas import (
    AuditLogEvent,
    BrokerOrder,
    Fill,
    OperationReport,
    OperatorNotification,
    OrderPlan,
    PortfolioPlan,
    Signal,
    StrategyApprovalTicket,
    StrategyDraft,
    StrategyPerformanceRecord,
    StrategyRecipe,
    TradeApprovalTicket,
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

    @staticmethod
    def _snapshot(item: T) -> T:
        if isinstance(item, BaseModel):
            return type(item).model_validate(item.model_dump())
        return deepcopy(item)

    def add(self, item: T) -> T:
        stored = self._snapshot(item)
        item_id = self._id_getter(stored)
        if item_id in self._items:
            raise RepositoryError(f"duplicate id: {item_id}")
        self._items[item_id] = stored
        return self._snapshot(stored)

    def get(self, item_id: str) -> T | None:
        item = self._items.get(item_id)
        return None if item is None else self._snapshot(item)

    def require(self, item_id: str) -> T:
        item = self.get(item_id)
        if item is None:
            raise RepositoryError(f"missing item: {item_id}")
        return item

    def list(self) -> list[T]:
        return [self._snapshot(item) for item in self._items.values()]

    def update(self, item: T) -> T:
        stored = self._snapshot(item)
        item_id = self._id_getter(stored)
        if item_id not in self._items:
            raise RepositoryError(f"cannot update missing item: {item_id}")
        self._items[item_id] = stored
        return self._snapshot(stored)

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
        self.approval_tickets = InMemoryRepository[TradeApprovalTicket](lambda item: item.ticket_id)
        self.strategy_approval_tickets = InMemoryRepository[StrategyApprovalTicket](lambda item: item.ticket_id)
        self.backtest_results = InMemoryRepository[BacktestResult](lambda item: item.result_id)
        self.strategy_drafts = InMemoryRepository[StrategyDraft](lambda item: item.draft_id)
        self.strategy_performance = InMemoryRepository[StrategyPerformanceRecord](lambda item: item.record_id)
        self.notifications = InMemoryRepository[OperatorNotification](lambda item: item.notification_id)

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
        self.approval_tickets.clear()
        self.strategy_approval_tickets.clear()
        self.backtest_results.clear()
        self.strategy_drafts.clear()
        self.strategy_performance.clear()
        self.notifications.clear()
