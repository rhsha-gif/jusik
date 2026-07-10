from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quantpilot.packages.brokers.kis_paper import (
    KisPaperBrokerAdapter,
    PaperPortfolioLossMetrics,
    SecurityMetadataSectorProvider,
)
from quantpilot.packages.core.kis_paper import (
    KisBalancePosition,
    KisBalanceResult,
    KisBalanceSummary,
    KisCurrentPrice,
    KisL2Snapshot as KisApiL2Snapshot,
    KisOrderBookLevel,
)
from quantpilot.packages.core.marketdata.kis_paper import KisPaperMarketDataProvider
from quantpilot.packages.core.schemas import (
    BrokerMode,
    BrokerOrder,
    Fill,
    OrderIntent,
    OrderPlan,
)


NOW = datetime(2026, 7, 10, 1, 0, 10, tzinfo=timezone.utc)


def _balance(*, net_assets: str = "1000000") -> KisBalanceResult:
    return KisBalanceResult(
        positions=(
            KisBalancePosition(
                symbol="005930",
                product_name="fixture security",
                holding_quantity=10,
                orderable_quantity=3,
                purchase_average_price=Decimal("68000"),
                current_price=Decimal("70000"),
                purchase_amount=Decimal("680000"),
                evaluation_amount=Decimal("700000"),
            ),
        ),
        summary=KisBalanceSummary(
            deposit_amount=Decimal("300000"),
            next_day_settlement_amount=Decimal("300000"),
            total_purchase_amount=Decimal("680000"),
            total_evaluation_amount=Decimal(net_assets),
            net_asset_amount=Decimal(net_assets),
            evaluation_profit_loss=Decimal("20000"),
        ),
        pages_fetched=1,
    )


class FakeClient:
    account_scope_fingerprint = "sha256:" + "a" * 64

    def __init__(self) -> None:
        self.balance = _balance()
        self.balance_calls = 0

    def get_balance(self, *, exchange: str = "KRX") -> KisBalanceResult:
        assert exchange == "KRX"
        self.balance_calls += 1
        return self.balance

    def get_current_price(
        self,
        symbol: str,
        *,
        exchange: str = "KRX",
    ) -> KisCurrentPrice:
        return KisCurrentPrice(
            symbol=symbol,
            last_price=Decimal("70000"),
            open_price=Decimal("69000"),
            high_price=Decimal("70500"),
            low_price=Decimal("68500"),
            accumulated_volume=1000,
        )

    def get_l2(
        self,
        symbol: str,
        *,
        exchange: str = "KRX",
    ) -> KisApiL2Snapshot:
        return KisApiL2Snapshot(
            symbol=symbol,
            accepted_at_hhmmss="100005",
            levels=tuple(
                KisOrderBookLevel(
                    level=index,
                    ask_price=Decimal(70000 + index * 100),
                    bid_price=Decimal(70000 - index * 100),
                    ask_quantity=100,
                    bid_quantity=100,
                )
                for index in range(1, 11)
            ),
        )


class FakeSessionAuthority:
    def current_open_session_date(self, _observed_at: datetime) -> date:
        return date(2026, 7, 10)


class FakeLossProvider:
    def __init__(self, *, as_of: datetime = NOW) -> None:
        self.as_of = as_of
        self.calls = 0

    def get_loss_metrics(
        self,
        balance: KisBalanceResult,
        *,
        observed_at: datetime,
    ) -> PaperPortfolioLossMetrics:
        assert balance.pages_fetched == 1
        assert observed_at == NOW
        self.calls += 1
        return PaperPortfolioLossMetrics(
            daily_loss_ratio=-0.01,
            monthly_loss_ratio=-0.02,
            as_of=self.as_of,
        )


class FakeSectorProvider:
    def __init__(self, sector: str | None = "technology") -> None:
        self.sector = sector

    def sector_for_symbol(self, symbol: str) -> str | None:
        assert symbol == "005930"
        return self.sector


class FakeSubmissionGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def submit_prepared_order(
        self,
        order_plan: OrderPlan,
    ) -> tuple[BrokerOrder, list[Fill]]:
        self.calls.append(order_plan.order_plan_id)
        broker_order = BrokerOrder(
            broker_order_id="paper-order-001",
            order_plan_id=order_plan.order_plan_id,
            broker_mode=BrokerMode.paper,
            broker_reference="1234567890",
            accepted_at=NOW,
        )
        return broker_order, []


def _order() -> OrderPlan:
    return OrderPlan(
        order_plan_id="plan-001",
        policy_id="policy-001",
        policy_version=1,
        intent=OrderIntent(
            symbol="005930",
            side="buy",
            quantity=1,
            limit_price=70000,
            notional=70000,
            target_weight=0.1,
            reason="fixture",
            quote_time=NOW,
        ),
        idempotency_key="fixture-plan-001",
    )


def _adapter(
    *,
    client: FakeClient | None = None,
    loss_provider: FakeLossProvider | None = None,
    gateway: FakeSubmissionGateway | None = None,
) -> KisPaperBrokerAdapter:
    selected_client = client or FakeClient()
    market_data = KisPaperMarketDataProvider(
        selected_client,
        session_authority=FakeSessionAuthority(),
        clock=lambda: NOW,
    )
    return KisPaperBrokerAdapter(
        selected_client,  # type: ignore[arg-type]
        submission_gateway=gateway or FakeSubmissionGateway(),
        loss_provider=loss_provider or FakeLossProvider(),
        sector_provider=FakeSectorProvider(),
        market_data_provider=market_data,
        clock=lambda: NOW,
    )


def test_balance_snapshot_requires_explicit_fresh_loss_metrics() -> None:
    client = FakeClient()
    losses = FakeLossProvider()
    adapter = _adapter(client=client, loss_provider=losses)

    snapshot = adapter.get_positions("paper-user")

    assert client.balance_calls == 1
    assert losses.calls == 1
    assert snapshot.user_id == "paper-user"
    assert snapshot.cash == 300000
    assert snapshot.equity == 1000000
    assert snapshot.daily_loss_ratio == -0.01
    assert snapshot.monthly_loss_ratio == -0.02
    assert snapshot.positions[0].symbol == "005930"
    assert snapshot.positions[0].quantity == 10
    assert snapshot.positions[0].orderable_quantity == 3
    assert snapshot.positions[0].sector == "technology"
    assert snapshot.source == "kis_paper_balance_reconciled"


def test_stale_or_future_loss_metrics_fail_closed() -> None:
    for as_of in (NOW - timedelta(seconds=61), NOW + timedelta(microseconds=1)):
        adapter = _adapter(loss_provider=FakeLossProvider(as_of=as_of))
        with pytest.raises(RuntimeError, match="loss metrics are not fresh"):
            adapter.get_positions("paper-user")


def test_account_identity_exposes_only_opaque_scope() -> None:
    account = _adapter().get_account("paper-user")

    assert account == {
        "user_id": "paper-user",
        "account_scope_fingerprint": "sha256:" + "a" * 64,
        "broker_mode": "paper",
        "broker_environment": "kis_paper",
    }
    assert "account_id" not in account


def test_quotes_use_the_fail_closed_market_data_adapter() -> None:
    assert _adapter().get_quote("005930") == 70000


def test_submit_delegates_only_to_the_durable_gateway() -> None:
    client = FakeClient()
    gateway = FakeSubmissionGateway()
    adapter = _adapter(client=client, gateway=gateway)
    order = _order()

    broker_order, fills = adapter.submit_order(order)

    assert gateway.calls == [order.order_plan_id]
    assert broker_order.broker_reference == "1234567890"
    assert fills == []
    assert client.balance_calls == 0


def test_inconsistent_balance_and_naive_clock_fail_closed() -> None:
    client = FakeClient()
    client.balance = _balance(net_assets="500000")
    with pytest.raises(ValueError, match="inconsistent with equity"):
        _adapter(client=client).get_positions("paper-user")

    adapter = KisPaperBrokerAdapter(
        FakeClient(),  # type: ignore[arg-type]
        submission_gateway=FakeSubmissionGateway(),
        loss_provider=FakeLossProvider(),
        sector_provider=FakeSectorProvider(),
        market_data_provider=KisPaperMarketDataProvider(
            FakeClient(),
            session_authority=FakeSessionAuthority(),
            clock=lambda: NOW,
        ),
        clock=lambda: NOW.replace(tzinfo=None),
    )
    with pytest.raises(RuntimeError, match="clock must include"):
        adapter.get_positions("paper-user")


def test_missing_sector_metadata_blocks_external_snapshot() -> None:
    client = FakeClient()
    market_data = KisPaperMarketDataProvider(
        client,
        session_authority=FakeSessionAuthority(),
        clock=lambda: NOW,
    )
    adapter = KisPaperBrokerAdapter(
        client,  # type: ignore[arg-type]
        submission_gateway=FakeSubmissionGateway(),
        loss_provider=FakeLossProvider(),
        sector_provider=FakeSectorProvider(None),
        market_data_provider=market_data,
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="sector metadata"):
        adapter.get_positions("paper-user")


def test_security_metadata_sector_provider_is_exact_and_fail_closed() -> None:
    provider = SecurityMetadataSectorProvider(
        [{"ticker": "005930", "sector": "technology"}]
    )

    assert provider.sector_for_symbol("005930") == "technology"
    assert provider.sector_for_symbol("000660") is None
    with pytest.raises(ValueError, match="authoritative sector"):
        SecurityMetadataSectorProvider(
            [{"ticker": "005930", "sector": "unknown"}]
        )
    with pytest.raises(ValueError, match="conflicting sectors"):
        SecurityMetadataSectorProvider(
            [
                {"ticker": "005930", "sector": "technology"},
                {"ticker": "005930", "sector": "industrial"},
            ]
        )
