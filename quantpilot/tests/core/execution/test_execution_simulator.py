from __future__ import annotations

from quantpilot.packages.core.execution.simulator import ExecutionSimulator
from quantpilot.packages.core.execution.types import (
    ExecutionSimulationRequest,
    ExecutionSimulatorConfig,
    ExecutionStatus,
    SlicingAlgorithm,
)
from quantpilot.packages.core.marketdata.types import (
    L2Snapshot,
    MarketDataQuality,
    ProviderStatus,
    Quote,
    QuoteSnapshot,
)
from quantpilot.packages.core.schemas import DataMode, OrderIntent, OrderPlan, OrderStatus, OrderType, UserPolicy


class RecordingQuoteProvider:
    def __init__(self, *, bid: float = 99.9, ask: float = 100.1, last: float = 100.0) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.bid = bid
        self.ask = ask
        self.last = last

    def get_quotes(self, symbols: list[str]) -> QuoteSnapshot:
        self.calls.append(tuple(symbols))
        symbol = symbols[0]
        return QuoteSnapshot(
            quotes={symbol: Quote(symbol=symbol, last=self.last, bid=self.bid, ask=self.ask)},
            provider_status=ProviderStatus(provider_name="test_quote", data_mode=DataMode.fixture),
            data_quality=MarketDataQuality(usable=True, symbol_count=1, data_mode=DataMode.fixture),
        )


class StaticL2Provider:
    def get_l2_snapshot(self, symbol: str) -> L2Snapshot:
        return L2Snapshot(
            symbol=symbol,
            bids=[{"price": 99.9, "quantity": 75.0}, {"price": 99.8, "quantity": 125.0}],
            asks=[{"price": 100.1, "quantity": 60.0}, {"price": 100.2, "quantity": 40.0}],
        )


def _approved_order(*, quantity: float = 100.0, order_type: OrderType = OrderType.limit) -> OrderPlan:
    policy = UserPolicy(allowed_order_types=[OrderType.limit, OrderType.market])
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        status=OrderStatus.user_approved,
        idempotency_key=f"sim-{order_type.value}-{quantity}",
        intent=OrderIntent(
            symbol="AAA",
            side="buy",
            order_type=order_type,
            quantity=quantity,
            limit_price=100.2 if order_type == OrderType.limit else None,
            notional=quantity * 100.2,
            target_weight=0.05,
            reason="execution simulator test",
        ),
    )


def test_simulator_returns_serializable_event_stream_with_partial_lifecycle() -> None:
    simulator = ExecutionSimulator(quote_provider=RecordingQuoteProvider(), l2_provider=StaticL2Provider())
    request = ExecutionSimulationRequest(
        order_plan=_approved_order(quantity=120),
        config=ExecutionSimulatorConfig(algorithm=SlicingAlgorithm.twap, slice_count=3),
    )

    result = simulator.simulate(request)

    assert result.status == ExecutionStatus.partially_filled
    assert result.broker_order_sent is False
    assert result.live_trading_enabled is False
    assert result.market_orders_enabled is False
    assert result.data_mode == DataMode.fixture
    assert result.filled_quantity > 0
    assert result.remaining_quantity > 0
    assert result.queue_ahead_quantity == 100.0
    assert result.adverse_selection_bps > 0
    assert result.estimated_slippage_bps >= 0
    assert {event.event_type for event in result.events} >= {
        "slice_scheduled",
        "broker_acceptance_simulated",
        "partial_fill",
        "queue_estimated",
        "adverse_selection_estimated",
    }
    assert result.model_dump(mode="json")["broker_order_sent"] is False


def test_cancel_replace_is_simulated_without_broker_call_path() -> None:
    quote_provider = RecordingQuoteProvider()
    simulator = ExecutionSimulator(quote_provider=quote_provider)
    request = ExecutionSimulationRequest(
        order_plan=_approved_order(quantity=40),
        config=ExecutionSimulatorConfig(
            algorithm=SlicingAlgorithm.twap,
            slice_count=2,
            simulate_cancel_replace=True,
            cancel_replace_at_slice=2,
        ),
    )

    result = simulator.simulate(request)

    assert result.broker_order_sent is False
    assert quote_provider.calls == [("AAA",)]
    assert [event.event_type for event in result.events if "cancel_replace" in event.event_type] == [
        "cancel_replace_requested",
        "cancel_replace_simulated",
    ]


def test_market_order_request_fails_closed_without_simulation() -> None:
    simulator = ExecutionSimulator(quote_provider=RecordingQuoteProvider())
    request = ExecutionSimulationRequest(
        order_plan=_approved_order(order_type=OrderType.market),
        config=ExecutionSimulatorConfig(algorithm=SlicingAlgorithm.twap),
    )

    result = simulator.simulate(request)

    assert result.status == ExecutionStatus.blocked
    assert result.filled_quantity == 0
    assert result.remaining_quantity == request.order_plan.intent.quantity
    assert result.broker_order_sent is False
    assert result.events[0].event_type == "blocked"
    assert result.events[0].reason_code == "market_order_disabled"
