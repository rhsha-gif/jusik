from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from quantpilot.packages.core.ledger.types import LedgerEntry, LedgerEventType
from quantpilot.packages.core.reports.paper_metrics import PaperTrialMetricsCalculator
from quantpilot.packages.core.risk.types import BatchPortfolioExposure, BatchRiskDecision
from quantpilot.packages.core.schemas import PortfolioPosition, PortfolioSnapshot


def _entry(
    *,
    event_type: LedgerEventType,
    order_plan_id: str,
    occurred_at: datetime,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    notional: float,
    metadata: dict[str, object] | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        event_type=event_type,
        policy_id="pol_metrics",
        policy_version=1,
        order_plan_id=order_plan_id,
        intent_id=f"intent_{order_plan_id}",
        broker_order_id=f"broker_{order_plan_id}" if event_type != LedgerEventType.order_intent else None,
        fill_id=f"fill_{order_plan_id}" if event_type == LedgerEventType.fill else None,
        idempotency_key=f"idem_{order_plan_id}",
        dedupe_key=f"{event_type.value}:idem_{order_plan_id}:{order_plan_id}",
        source="mock",
        data_mode="fixture",
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        quantity=quantity,
        price=price,
        notional=notional,
        metadata=metadata or {},
        occurred_at=occurred_at,
    )


def test_calculates_paper_trial_metrics_from_ledger() -> None:
    started_at = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    entries = [
        _entry(
            event_type=LedgerEventType.order_intent,
            order_plan_id="oplan_buy",
            occurred_at=started_at,
            symbol="AAA",
            side="buy",
            quantity=100,
            price=10,
            notional=1_000,
        ),
        _entry(
            event_type=LedgerEventType.submitted,
            order_plan_id="oplan_buy",
            occurred_at=started_at + timedelta(seconds=5),
            symbol="AAA",
            side="buy",
            quantity=100,
            price=10,
            notional=1_000,
        ),
        _entry(
            event_type=LedgerEventType.fill,
            order_plan_id="oplan_buy",
            occurred_at=started_at + timedelta(seconds=20),
            symbol="AAA",
            side="buy",
            quantity=100,
            price=10.10,
            notional=1_010,
        ),
        _entry(
            event_type=LedgerEventType.order_intent,
            order_plan_id="oplan_sell",
            occurred_at=started_at,
            symbol="BBB",
            side="sell",
            quantity=50,
            price=20,
            notional=1_000,
        ),
        _entry(
            event_type=LedgerEventType.submitted,
            order_plan_id="oplan_sell",
            occurred_at=started_at + timedelta(seconds=5),
            symbol="BBB",
            side="sell",
            quantity=50,
            price=20,
            notional=1_000,
        ),
        _entry(
            event_type=LedgerEventType.fill,
            order_plan_id="oplan_sell",
            occurred_at=started_at + timedelta(seconds=40),
            symbol="BBB",
            side="sell",
            quantity=50,
            price=19.80,
            notional=990,
        ),
        _entry(
            event_type=LedgerEventType.order_intent,
            order_plan_id="oplan_reject",
            occurred_at=started_at,
            symbol="CCC",
            side="buy",
            quantity=5,
            price=100,
            notional=500,
        ),
        _entry(
            event_type=LedgerEventType.reject,
            order_plan_id="oplan_reject",
            occurred_at=started_at + timedelta(seconds=10),
            symbol="CCC",
            side="buy",
            quantity=5,
            price=100,
            notional=500,
            metadata={"reason": "max_daily_turnover_after_batch"},
        ),
    ]
    snapshot = PortfolioSnapshot(
        cash=3_000,
        equity=10_000,
        positions=[
            PortfolioPosition(symbol="AAA", quantity=100, market_price=10, sector="tech"),
            PortfolioPosition(symbol="BBB", quantity=100, market_price=20, sector="industrial"),
            PortfolioPosition(symbol="DDD", quantity=40, market_price=100, sector="cash_proxy"),
        ],
        captured_at=started_at,
    )
    risk_decision = BatchRiskDecision(
        passed=False,
        mode="rejected",
        policy_version=1,
        rejected_order_plan_ids=["oplan_reject"],
        failed_checks=["max_daily_turnover_after_batch"],
        rejected_reasons={"oplan_reject": ["max_daily_turnover_after_batch"]},
        portfolio_after_batch=BatchPortfolioExposure(
            cash=2_500,
            equity=10_000,
            cash_weight=0.25,
            position_values={},
            position_weights={},
            sector_values={},
            sector_weights={},
        ),
    )

    metrics = PaperTrialMetricsCalculator().calculate(
        ledger_entries=entries,
        target_weights={"AAA": 0.25, "BBB": 0.05},
        target_cash_weight=0.20,
        snapshot=snapshot,
        batch_risk_decisions=[risk_decision],
        signal_timestamps={"oplan_buy": started_at, "oplan_sell": started_at + timedelta(seconds=10)},
        max_daily_turnover=3_000,
        single_order_cash_limit=1_200,
    )

    assert metrics.status == "available"
    assert metrics.turnover_notional == 2_000
    assert metrics.turnover_weight == 0.2
    assert metrics.execution_quality.fill_ratio == 0.8
    assert metrics.execution_quality.submitted_fill_ratio == 1.0
    assert metrics.execution_quality.average_slippage_bps == 100.0
    assert metrics.exposure_drift == 0.1
    assert metrics.cash_drag == 0.098
    assert metrics.rejected_reasons.reasons == {"max_daily_turnover_after_batch": 1}
    assert metrics.risk_budget_usage.daily_turnover_usage == 0.833333
    assert metrics.risk_budget_usage.largest_order_usage == 0.833333
    assert metrics.risk_budget_usage.failed_check_counts == {"max_daily_turnover_after_batch": 1}
    assert metrics.execution_quality.signal_to_fill_latency_seconds == 25.0
    assert metrics.execution_quality.latency_status == "available"
    assert metrics.live_trading_enabled is False
    json.dumps(metrics.model_dump(mode="json"))


def test_missing_ledger_returns_unavailable_metrics() -> None:
    metrics = PaperTrialMetricsCalculator().calculate(ledger_entries=[])

    assert metrics.status == "unavailable"
    assert metrics.unavailable_reason == "missing_ledger"
    assert metrics.execution_quality.latency_status == "unavailable"
    assert metrics.turnover_notional == 0
    assert metrics.live_trading_enabled is False
    json.dumps(metrics.model_dump(mode="json"))
