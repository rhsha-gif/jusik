from __future__ import annotations

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.core.schemas import BrokerMode, BrokerOrder, Fill, OrderPlan


class PaperBroker(MockBroker):
    mode = BrokerMode.paper.value

    def __init__(self) -> None:
        super().__init__()
        self.live_api_calls = 0

    def submit_order(self, order_plan: OrderPlan) -> tuple[BrokerOrder, list[Fill]]:
        price = order_plan.intent.limit_price or self.get_quote(order_plan.intent.symbol)
        broker_order = BrokerOrder(
            order_plan_id=order_plan.order_plan_id,
            broker_mode=BrokerMode.paper,
            broker_reference=f"paper-{order_plan.order_plan_id}",
        )
        if order_plan.intent.notional > 750_000:
            first_quantity = round(order_plan.intent.quantity * 0.5, 6)
            second_quantity = round(order_plan.intent.quantity - first_quantity, 6)
            fills = [
                Fill(
                    broker_order_id=broker_order.broker_order_id,
                    order_plan_id=order_plan.order_plan_id,
                    symbol=order_plan.intent.symbol,
                    quantity=first_quantity,
                    price=price,
                    notional=round(first_quantity * price, 2),
                ),
                Fill(
                    broker_order_id=broker_order.broker_order_id,
                    order_plan_id=order_plan.order_plan_id,
                    symbol=order_plan.intent.symbol,
                    quantity=second_quantity,
                    price=price,
                    notional=round(second_quantity * price, 2),
                ),
            ]
            return broker_order, fills
        fill = Fill(
            broker_order_id=broker_order.broker_order_id,
            order_plan_id=order_plan.order_plan_id,
            symbol=order_plan.intent.symbol,
            quantity=order_plan.intent.quantity,
            price=price,
            notional=round(order_plan.intent.quantity * price, 2),
        )
        return broker_order, [fill]
