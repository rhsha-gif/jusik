from __future__ import annotations

from typing import Protocol

from quantpilot.packages.core.schemas import BrokerOrder, Fill, OrderPlan, PortfolioSnapshot


class BrokerAdapter(Protocol):
    mode: str

    def get_account(self, user_id: str) -> dict[str, str]: ...

    def get_cash(self, user_id: str) -> float: ...

    def get_positions(self, user_id: str) -> PortfolioSnapshot: ...

    def get_quote(self, symbol: str) -> float: ...

    def submit_order(self, order_plan: OrderPlan) -> tuple[BrokerOrder, list[Fill]]: ...
