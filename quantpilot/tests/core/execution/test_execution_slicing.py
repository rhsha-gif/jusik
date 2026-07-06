from __future__ import annotations

from datetime import timedelta

from quantpilot.packages.core.execution.slicing import build_slice_schedule
from quantpilot.packages.core.execution.types import ExecutionSimulatorConfig, SlicingAlgorithm
from quantpilot.packages.core.schemas import OrderIntent, OrderPlan, OrderStatus, OrderType, UserPolicy, utc_now


def _approved_order(*, quantity: float = 100.0) -> OrderPlan:
    policy = UserPolicy()
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        status=OrderStatus.user_approved,
        idempotency_key="slice-test-key",
        intent=OrderIntent(
            symbol="AAA",
            side="buy",
            order_type=OrderType.limit,
            quantity=quantity,
            limit_price=100.0,
            notional=quantity * 100.0,
            target_weight=0.05,
            reason="execution slicing test",
        ),
    )


def test_twap_schedule_splits_quantity_evenly() -> None:
    order = _approved_order(quantity=100)
    start = utc_now()

    schedule = build_slice_schedule(
        order,
        ExecutionSimulatorConfig(
            algorithm=SlicingAlgorithm.twap,
            slice_count=4,
            interval_seconds=30,
            start_at=start,
        ),
    )

    assert [slice_.quantity for slice_ in schedule.slices] == [25.0, 25.0, 25.0, 25.0]
    assert [slice_.scheduled_at for slice_ in schedule.slices] == [
        start,
        start + timedelta(seconds=30),
        start + timedelta(seconds=60),
        start + timedelta(seconds=90),
    ]
    assert schedule.total_scheduled_quantity == 100.0


def test_vwap_schedule_uses_normalized_volume_curve() -> None:
    order = _approved_order(quantity=120)

    schedule = build_slice_schedule(
        order,
        ExecutionSimulatorConfig(
            algorithm=SlicingAlgorithm.vwap,
            slice_count=3,
            volume_curve=[1, 2, 1],
        ),
    )

    assert [slice_.quantity for slice_ in schedule.slices] == [30.0, 60.0, 30.0]
    assert schedule.total_scheduled_quantity == 120.0


def test_pov_schedule_respects_participation_cap() -> None:
    order = _approved_order(quantity=1_000)

    schedule = build_slice_schedule(
        order,
        ExecutionSimulatorConfig(
            algorithm=SlicingAlgorithm.pov,
            slice_count=3,
            max_participation_rate=0.10,
            expected_slice_volumes=[200, 500, 800],
        ),
    )

    assert [slice_.quantity for slice_ in schedule.slices] == [20.0, 50.0, 80.0]
    assert [slice_.participation_rate for slice_ in schedule.slices] == [0.10, 0.10, 0.10]
    assert schedule.total_scheduled_quantity == 150.0
    assert schedule.unscheduled_quantity == 850.0
