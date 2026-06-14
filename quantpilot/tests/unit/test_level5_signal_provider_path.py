from __future__ import annotations

from typing import Any

import pytest

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.marketdata import FakeOHLCVProvider, FakeQuoteProvider
from quantpilot.packages.core.operator.schemas import OperatorRunRequest
from quantpilot.packages.core.operator.service import OperatorService
from quantpilot.packages.core.schemas import BrokerMode, ExecutionMode, UserPolicy
from quantpilot.packages.core.signals.service import load_fixture_ohlcv
from quantpilot.packages.core.strategies.promotion import load_lifecycle_fixture
from quantpilot.packages.core.strategies.registry import StrategyRegistry, StrategyRegistryEntry


class StaticHarnessMarketDataProvider:
    def __init__(self, bars: list[dict[str, Any]]) -> None:
        self._bars = [dict(bar) for bar in bars]

    def get_bars(self) -> list[dict[str, Any]]:
        return [dict(bar) for bar in self._bars]

    def get_price_history(self) -> list[dict[str, Any]]:
        return []


def _promoted_policy() -> UserPolicy:
    return UserPolicy(
        version=5,
        execution_mode=ExecutionMode.fully_automated,
        broker=BrokerMode.mock,
        authority_level=5,
        fully_automated_operator_enabled=True,
    )


def _level5_registry() -> StrategyRegistry:
    return StrategyRegistry(
        [
            StrategyRegistryEntry(
                strategy_id="pullback_trend_v1",
                version="1.0.0",
                spec_hash="sha256:fixture-pullback-trend-v1-validated-snapshot",
                status="validated_l5",
                allowed_execution_levels=["level_5", "fully_automated"],
                priority=10,
            ),
        ],
        lifecycle_records=load_lifecycle_fixture(),
    )


def _request(policy: UserPolicy, *, key: str = "provider-bound-level5") -> OperatorRunRequest:
    return OperatorRunRequest(
        policy_id=policy.policy_id,
        requested_policy_version=policy.version,
        run_mode="dry_run",
        idempotency_key=key,
    )


def test_level5_operator_signal_path_uses_injected_provider_not_fixture_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = load_fixture_ohlcv()
    policy = _promoted_policy()
    harness = HarnessService(market_data_provider=StaticHarnessMarketDataProvider(bars))
    service = OperatorService(
        harness,
        registry=_level5_registry(),
        ohlcv_provider=FakeOHLCVProvider(bars),
        quote_provider=FakeQuoteProvider.from_bars(bars),
    )
    service.repositories.policies.add(policy)

    def fail_if_called(*args: object, **kwargs: object) -> list[dict[str, Any]]:
        raise AssertionError("Level 5 operator must use the injected provider")

    monkeypatch.setattr("quantpilot.packages.core.signals.service.load_fixture_ohlcv", fail_if_called)

    result = service.run_once(_request(policy))

    assert result.status == "completed"
    assert result.report.order_plan_ids
    assert service.repositories.broker_orders.list() == []


def test_level5_operator_does_not_plan_orders_when_provider_is_stale() -> None:
    bars = load_fixture_ohlcv()
    policy = _promoted_policy()
    harness = HarnessService(market_data_provider=StaticHarnessMarketDataProvider(bars))
    service = OperatorService(
        harness,
        registry=_level5_registry(),
        ohlcv_provider=FakeOHLCVProvider.stale(bars),
        quote_provider=FakeQuoteProvider.from_bars(bars),
    )
    service.repositories.policies.add(policy)

    result = service.run_once(_request(policy, key="stale-provider-level5"))

    assert result.status == "completed"
    assert result.submitted_order_plan_ids == []
    assert result.report.order_plan_ids == []
    assert service.repositories.order_plans.list() == []
    assert {signal.action.value for signal in service.repositories.signals.list()} == {"blocked"}
