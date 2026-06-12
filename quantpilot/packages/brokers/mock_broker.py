from __future__ import annotations

from quantpilot.packages.brokers.base import BrokerAdapter
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import BrokerMode, BrokerOrder, Fill, OrderPlan, PortfolioSnapshot


class MockBroker(BrokerAdapter):
    mode = BrokerMode.mock.value

    def __init__(self) -> None:
        self._quotes = {
            "AAA": 105.0,
            "BBB": 102.0,
            "CCC": 100.0,
            "DDD": 100.0,
            "EEE": 100.0,
            "FFF": 95.0,
            "GGG": 50.0,
        }

    def get_account(self, user_id: str) -> dict[str, str]:
        return {"user_id": user_id, "account_id": "mock-account", "broker_mode": self.mode}

    def get_cash(self, user_id: str) -> float:
        return fixture_portfolio_snapshot().cash

    def get_positions(self, user_id: str) -> PortfolioSnapshot:
        return fixture_portfolio_snapshot()

    def get_quote(self, symbol: str) -> float:
        return self._quotes.get(symbol, 100.0)

    def submit_order(self, order_plan: OrderPlan) -> tuple[BrokerOrder, list[Fill]]:
        price = order_plan.intent.limit_price or self.get_quote(order_plan.intent.symbol)
        broker_order = BrokerOrder(
            order_plan_id=order_plan.order_plan_id,
            broker_mode=BrokerMode.mock,
            broker_reference=f"mock-{order_plan.order_plan_id}",
        )
        fill = Fill(
            broker_order_id=broker_order.broker_order_id,
            order_plan_id=order_plan.order_plan_id,
            symbol=order_plan.intent.symbol,
            quantity=order_plan.intent.quantity,
            price=price,
            notional=round(order_plan.intent.quantity * price, 2),
        )
        return broker_order, [fill]
