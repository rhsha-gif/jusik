from __future__ import annotations

from datetime import timedelta

from quantpilot.packages.core.execution.types import (
    ExecutionSimulatorConfig,
    ExecutionSlice,
    SliceSchedule,
    SlicingAlgorithm,
)
from quantpilot.packages.core.schemas import OrderPlan


def _round_quantity(value: float) -> float:
    return round(max(value, 0.0), 6)


def _weights_for_count(values: list[float], count: int) -> list[float]:
    if not values:
        return [1.0] * count
    if len(values) >= count:
        return values[:count]
    return values + [values[-1]] * (count - len(values))


def _weighted_quantities(total_quantity: float, weights: list[float]) -> list[float]:
    total_weight = sum(weights)
    if total_weight <= 0:
        weights = [1.0] * len(weights)
        total_weight = float(len(weights))

    quantities: list[float] = []
    allocated = 0.0
    for weight in weights[:-1]:
        quantity = _round_quantity(total_quantity * (weight / total_weight))
        quantities.append(quantity)
        allocated += quantity
    quantities.append(_round_quantity(total_quantity - allocated))
    return quantities


def _expected_volumes_for_count(values: list[float], count: int, fallback_volume: float) -> list[float]:
    if not values:
        return [fallback_volume] * count
    if len(values) >= count:
        return values[:count]
    return values + [values[-1]] * (count - len(values))


def build_slice_schedule(order_plan: OrderPlan, config: ExecutionSimulatorConfig) -> SliceSchedule:
    quantity = _round_quantity(order_plan.intent.quantity)
    scheduled_at = [
        config.start_at + timedelta(seconds=config.interval_seconds * index)
        for index in range(config.slice_count)
    ]

    slices: list[ExecutionSlice] = []
    if config.algorithm in {SlicingAlgorithm.twap, SlicingAlgorithm.vwap}:
        weights = [1.0] * config.slice_count
        if config.algorithm == SlicingAlgorithm.vwap:
            weights = _weights_for_count(config.volume_curve, config.slice_count)
        quantities = _weighted_quantities(quantity, weights)
        slices = [
            ExecutionSlice(
                slice_id=index + 1,
                scheduled_at=scheduled_at[index],
                quantity=slice_quantity,
            )
            for index, slice_quantity in enumerate(quantities)
        ]
    else:
        fallback_volume = quantity / config.max_participation_rate / config.slice_count
        expected_volumes = _expected_volumes_for_count(
            config.expected_slice_volumes,
            config.slice_count,
            fallback_volume,
        )
        remaining = quantity
        for index, expected_volume in enumerate(expected_volumes):
            if remaining <= 0:
                slice_quantity = 0.0
            else:
                slice_quantity = min(remaining, expected_volume * config.max_participation_rate)
            slice_quantity = _round_quantity(slice_quantity)
            remaining = _round_quantity(remaining - slice_quantity)
            participation_rate = (
                round(slice_quantity / expected_volume, 6)
                if expected_volume > 0
                else 0.0
            )
            slices.append(
                ExecutionSlice(
                    slice_id=index + 1,
                    scheduled_at=scheduled_at[index],
                    quantity=slice_quantity,
                    expected_volume=expected_volume,
                    participation_rate=participation_rate,
                )
            )

    total_scheduled = _round_quantity(sum(slice_.quantity for slice_ in slices))
    return SliceSchedule(
        order_plan_id=order_plan.order_plan_id,
        algorithm=config.algorithm,
        slices=slices,
        total_requested_quantity=quantity,
        total_scheduled_quantity=total_scheduled,
        unscheduled_quantity=_round_quantity(quantity - total_scheduled),
    )
