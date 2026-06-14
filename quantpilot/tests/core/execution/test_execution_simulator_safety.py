from __future__ import annotations

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.core.execution.simulator import ExecutionSimulator
from quantpilot.packages.core.execution.types import ExecutionSimulationRequest, ExecutionSimulatorConfig, ExecutionStatus
from quantpilot.packages.core.marketdata.types import MarketDataQuality, ProviderStatus, QuoteSnapshot
from quantpilot.packages.core.schemas import DataMode, OrderIntent, OrderPlan, OrderStatus, OrderType, UserPolicy


class UnavailableQuoteProvider:
    def get_quotes(self, symbols: list[str]) -> QuoteSnapshot:
        del symbols
        return QuoteSnapshot(
            quotes={},
            provider_status=ProviderStatus(
                provider_name="unavailable_test_quote",
                state="unavailable",
                reason="unit test unavailable",
                data_mode=DataMode.fixture,
            ),
            data_quality=MarketDataQuality(
                usable=False,
                degraded=True,
                reason_codes=["provider_unavailable"],
                symbol_count=0,
                data_mode=DataMode.fixture,
            ),
        )


def _order(*, status: OrderStatus = OrderStatus.user_approved) -> OrderPlan:
    policy = UserPolicy()
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        status=status,
        idempotency_key=f"safety-{status.value}",
        intent=OrderIntent(
            symbol="AAA",
            side="buy",
            order_type=OrderType.limit,
            quantity=10,
            limit_price=100,
            notional=1_000,
            target_weight=0.01,
            reason="execution simulator safety test",
        ),
    )


def test_non_approved_order_fails_closed() -> None:
    result = ExecutionSimulator(quote_provider=UnavailableQuoteProvider()).simulate(
        ExecutionSimulationRequest(order_plan=_order(status=OrderStatus.proposed))
    )

    assert result.status == ExecutionStatus.blocked
    assert result.events[0].reason_code == "order_not_approved"
    assert result.broker_order_sent is False
    assert result.live_trading_enabled is False


def test_unavailable_quote_provider_returns_unavailable_no_trade_result() -> None:
    request = ExecutionSimulationRequest(order_plan=_order())

    result = ExecutionSimulator(quote_provider=UnavailableQuoteProvider()).simulate(request)

    assert result.status == ExecutionStatus.unavailable
    assert result.filled_quantity == 0
    assert result.remaining_quantity == request.order_plan.intent.quantity
    assert result.events[0].event_type == "unavailable"
    assert result.events[0].reason_code == "quote_unavailable"
    assert result.broker_order_sent is False


def test_existing_mock_broker_immediate_fill_behavior_is_unchanged() -> None:
    order = _order()

    broker_order, fills = MockBroker().submit_order(order)

    assert broker_order.broker_mode.value == "mock"
    assert len(fills) == 1
    assert fills[0].quantity == order.intent.quantity
