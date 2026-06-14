from __future__ import annotations

from typing import Protocol, runtime_checkable

from quantpilot.packages.brokers.base import BrokerAdapter
from quantpilot.packages.brokers.paper_broker import PaperBroker
from quantpilot.packages.core.schemas import BrokerMode, BrokerOrder, Fill, OrderPlan, OrderStatus, PortfolioSnapshot


class PaperBrokerConfigError(ValueError):
    """Raised when a paper broker adapter is configured with an unsafe client."""


@runtime_checkable
class PaperBrokerClient(Protocol):
    is_fake_client: bool

    def get_account(self, user_id: str) -> dict[str, str]: ...

    def get_cash(self, user_id: str) -> float: ...

    def get_positions(self, user_id: str) -> PortfolioSnapshot: ...

    def get_quote(self, symbol: str) -> float: ...

    def place_order(self, order_plan: OrderPlan) -> tuple[BrokerOrder, list[Fill]]: ...

    def get_order(self, broker_reference: str) -> BrokerOrder: ...

    def cancel_order(self, broker_reference: str) -> BrokerOrder: ...


class FakePaperBrokerClient:
    is_fake_client = True

    def __init__(self) -> None:
        self._broker = PaperBroker()
        self._orders_by_reference: dict[str, BrokerOrder] = {}

    def get_account(self, user_id: str) -> dict[str, str]:
        return {"user_id": user_id, "account_id": "paper-fake-account", "broker_mode": BrokerMode.paper.value}

    def get_cash(self, user_id: str) -> float:
        return self._broker.get_cash(user_id)

    def get_positions(self, user_id: str) -> PortfolioSnapshot:
        return self._broker.get_positions(user_id)

    def get_quote(self, symbol: str) -> float:
        return self._broker.get_quote(symbol)

    def place_order(self, order_plan: OrderPlan) -> tuple[BrokerOrder, list[Fill]]:
        broker_order, fills = self._broker.submit_order(order_plan)
        broker_order.broker_reference = broker_order.broker_reference or broker_order.broker_order_id
        self._orders_by_reference[broker_order.broker_reference] = broker_order
        self._orders_by_reference[broker_order.broker_order_id] = broker_order
        return broker_order, fills

    def get_order(self, broker_reference: str) -> BrokerOrder:
        try:
            return self._orders_by_reference[broker_reference]
        except KeyError:
            raise LookupError(f"paper order not found: {broker_reference}")

    def cancel_order(self, broker_reference: str) -> BrokerOrder:
        order = self.get_order(broker_reference)
        cancelled = order.model_copy(update={"status": OrderStatus.cancelled})
        self._orders_by_reference[broker_reference] = cancelled
        self._orders_by_reference[cancelled.broker_order_id] = cancelled
        if cancelled.broker_reference:
            self._orders_by_reference[cancelled.broker_reference] = cancelled
        return cancelled


class PaperBrokerAdapter(BrokerAdapter):
    mode = BrokerMode.paper.value

    def __init__(self, client: PaperBrokerClient | None = None) -> None:
        self._client = client or FakePaperBrokerClient()
        if not getattr(self._client, "is_fake_client", False):
            raise PaperBrokerConfigError("paper adapter requires an explicit fake/manual client in this harness")

    def __repr__(self) -> str:
        return "PaperBrokerAdapter(client=<redacted>)"

    def get_account(self, user_id: str) -> dict[str, str]:
        return self._client.get_account(user_id)

    def get_cash(self, user_id: str) -> float:
        return self._client.get_cash(user_id)

    def get_positions(self, user_id: str) -> PortfolioSnapshot:
        return self._client.get_positions(user_id)

    def get_quote(self, symbol: str) -> float:
        return self._client.get_quote(symbol)

    def submit_order(self, order_plan: OrderPlan) -> tuple[BrokerOrder, list[Fill]]:
        broker_order, fills = self._client.place_order(order_plan)
        if broker_order.broker_mode != BrokerMode.paper:
            raise PaperBrokerConfigError("paper adapter client returned a non-paper broker order")
        return broker_order, fills

    def get_order_status(self, broker_order_id: str) -> BrokerOrder:
        return self._client.get_order(broker_order_id)

    def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        return self._client.cancel_order(broker_order_id)
