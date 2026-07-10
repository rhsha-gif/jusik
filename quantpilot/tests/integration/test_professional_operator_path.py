from __future__ import annotations

from datetime import date, timedelta

import pytest

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.marketdata.fake_provider import FakeOHLCVProvider
from quantpilot.packages.core.marketdata.types import (
    MarketDataQuality,
    ProviderStatus,
    Quote,
    QuoteSnapshot,
)
from quantpilot.packages.core.operator.schemas import OperatorRunRequest
from quantpilot.packages.core.operator.service import OperatorService
from quantpilot.packages.core.operator.position_ledger import StrategyOperatorState
from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    OrderStatus,
    UserPolicy,
    utc_now,
)
from quantpilot.packages.core.signals.types import MultiFactorScore
from quantpilot.packages.core.strategies.promotion import load_lifecycle_fixture
from quantpilot.packages.core.strategies.registry import StrategyRegistry, StrategyRegistryEntry
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


class FixedQuoteProvider:
    def __init__(self, quote: Quote) -> None:
        self.quote = quote

    def get_quotes(self, symbols: list[str]) -> QuoteSnapshot:
        selected = {self.quote.symbol: self.quote} if self.quote.symbol in symbols else {}
        return QuoteSnapshot(
            quotes=selected,
            provider_status=ProviderStatus(provider_name="fixed_quote"),
            data_quality=MarketDataQuality(usable=True, symbol_count=len(selected)),
        )


def _bars() -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    closes = [100.0 + index * 0.20 for index in range(120)]
    closes.extend([closes[-1] - offset for offset in range(1, 15)])
    closes.append(closes[-1] + 8.0)
    return [
        {
            "symbol": "AAA",
            "ticker": "AAA",
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close - 0.2,
            "high": close + 0.8,
            "low": close - 0.8,
            "close": close,
            "volume": 100_000 if index < len(closes) - 1 else 120_000,
        }
        for index, close in enumerate(closes)
    ]


def _score(symbol: str) -> MultiFactorScore:
    return MultiFactorScore(
        symbol=symbol,
        momentum=75.0,
        trend=75.0,
        volume=75.0,
        volatility=75.0,
        data_quality=100.0,
        final_score=75.0,
        regime="uptrend",
        weights={
            "momentum": 0.24,
            "trend": 0.30,
            "volume": 0.18,
            "volatility": 0.16,
            "data_quality": 0.12,
        },
        reason_codes=["regime_uptrend"],
    )


def test_selected_professional_strategy_uses_history_snapshot_and_actual_quote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FULLY_AUTOMATED_OPERATOR_ENABLED", "true")
    monkeypatch.setattr(
        "quantpilot.packages.core.signals.service.build_multi_factor_score",
        lambda signal, **_: _score(signal.symbol),
    )
    monkeypatch.setattr(
        "quantpilot.packages.core.execution.state_machine.is_krx_auto_order_window",
        lambda _now=None: True,
    )
    quote_time = utc_now()
    bars = _bars()
    quote = Quote(symbol="AAA", last=float(bars[-1]["close"]), as_of=quote_time)
    lifecycle = next(
        record
        for record in load_lifecycle_fixture()
        if record.strategy_id == "level5_candidate_fixture"
    ).model_copy(
        update={
            "strategy_id": "pullback_trend_v2",
            "version": "2.0",
            "spec_hash": "sha256:test-professional-v2",
        }
    )
    registry = StrategyRegistry(
        [
            StrategyRegistryEntry(
                strategy_id="pullback_trend_v2",
                version="2.0",
                spec_hash="sha256:test-professional-v2",
                status="validated_l5",
                allowed_execution_levels=["level_5", "fully_automated"],
                priority=1,
            )
        ],
        lifecycle_records=[lifecycle],
    )
    harness = HarnessService()
    policy = UserPolicy(
        version=5,
        execution_mode=ExecutionMode.fully_automated,
        broker=BrokerMode.mock,
        authority_level=5,
        fully_automated_operator_enabled=True,
    )
    harness.repositories.policies.add(policy)
    with PaperStateStore(tmp_path / "professional-state.sqlite3") as store:
        store.save_strategy_operator_state(
            StrategyOperatorState(
                policy_id=policy.policy_id,
                strategy_id="pullback_trend_v2",
                strategy_version="2.0",
                health_status="active",
                reason_codes=["healthy"],
                last_risk_evaluated_at=quote_time,
                updated_at=quote_time,
            )
        )
        service = OperatorService(
            harness,
            registry=registry,
            ohlcv_provider=FakeOHLCVProvider(bars),
            quote_provider=FixedQuoteProvider(quote),
            professional_state_store=store,
        )

        request = OperatorRunRequest(
            policy_id=policy.policy_id,
            requested_policy_version=policy.version,
            run_mode="dry_run",
            idempotency_key="professional-v2-selected-path",
        )
        result = service.run_once(
            request,
            now=quote_time,
        )
        claims_after_dry_run = store.list_operator_cycle_claims()
        dry_order_ids = {
            order.order_plan_id
            for order in service.repositories.order_plans.list()
        }
        dry_plans = service.repositories.portfolio_plans.list()
        order_count = len(service.repositories.order_plans.list())
        restarted = OperatorService(
            HarnessService(harness.repositories),
            registry=registry,
            ohlcv_provider=FakeOHLCVProvider(bars),
            quote_provider=FixedQuoteProvider(quote),
            professional_state_store=store,
        )
        replayed = restarted.run_once(request, now=quote_time)
        conflicting_restart = OperatorService(
            HarnessService(harness.repositories),
            registry=registry,
            ohlcv_provider=FakeOHLCVProvider(bars),
            quote_provider=FixedQuoteProvider(quote),
            professional_state_store=store,
        )
        with pytest.raises(ValueError, match="different durable request"):
            conflicting_restart.run_once(
                request.model_copy(update={"user_id": "other-user"}),
                now=quote_time,
            )
        order_count_after_replay = len(
            restarted.repositories.order_plans.list()
        )
        actual_request = request.model_copy(
            update={
                "run_mode": "mock_submit",
                "idempotency_key": "professional-v2-after-dry-run",
            }
        )
        actual = restarted.run_once(actual_request)

    assert result.status == "completed"
    assert result.report.strategy_selection.selected_strategy_id == "pullback_trend_v2"
    assert result.report.order_plan_ids
    assert replayed == result
    assert order_count_after_replay == order_count
    assert claims_after_dry_run == []
    assert actual.status == "completed", {
        "actual": actual.model_dump(mode="json"),
        "orders": [
            order.model_dump(mode="json")
            for order in service.repositories.order_plans.list()
        ],
    }
    assert actual.submitted_order_plan_ids
    assert all(
        service.repositories.order_plans.require(order_id).status
        == OrderStatus.cancelled
        for order_id in dry_order_ids
    )
    generated = service.repositories.signals.list()
    assert {signal.strategy_id for signal in generated} == {"pullback_trend_v2"}
    assert {signal.source for signal in generated} == {"professional_pullback_trend_v2"}
    assert len(dry_plans) == 1
    assert len(dry_plans[0].order_intents) == 1
    assert dry_plans[0].order_intents[0].limit_price == quote.last
    assert dry_plans[0].order_intents[0].quote_time == quote_time


def test_policy_version_mismatch_request_replays_exactly_after_restart(
    tmp_path,
) -> None:
    harness = HarnessService()
    policy = UserPolicy(
        version=2,
        execution_mode=ExecutionMode.fully_automated,
        broker=BrokerMode.mock,
        authority_level=5,
        fully_automated_operator_enabled=True,
    )
    harness.repositories.policies.add(policy)
    request = OperatorRunRequest(
        policy_id=policy.policy_id,
        requested_policy_version=1,
        run_mode="mock_submit",
        idempotency_key="policy-version-mismatch-replay",
    )
    now = utc_now()
    with PaperStateStore(tmp_path / "version-replay.sqlite3") as store:
        first = OperatorService(
            harness,
            professional_state_store=store,
        ).run_once(request, now=now)
        replayed = OperatorService(
            HarnessService(harness.repositories),
            professional_state_store=store,
        ).run_once(request, now=now)
        checkpoint = store.find_run_checkpoint_by_idempotency_key(
            request.idempotency_key
        )

    assert first.fallback is not None
    assert first.fallback.reason_code == "policy_review_required"
    assert replayed == first
    assert checkpoint is not None
    assert checkpoint.policy_version == request.requested_policy_version
    assert first.report.policy_version == policy.version
