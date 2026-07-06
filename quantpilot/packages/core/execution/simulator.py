from __future__ import annotations

from quantpilot.packages.core.execution.slicing import build_slice_schedule
from quantpilot.packages.core.execution.types import (
    ExecutionEvent,
    ExecutionSimulationRequest,
    ExecutionSimulationResult,
    ExecutionSimulatorConfig,
    ExecutionStatus,
    SliceSchedule,
)
from quantpilot.packages.core.marketdata.providers import L2Provider, QuoteProvider
from quantpilot.packages.core.marketdata.types import L2Snapshot, Quote, QuoteSnapshot
from quantpilot.packages.core.schemas import OrderPlan, OrderStatus, OrderType


class ExecutionSimulator:
    """Simulator-only execution lifecycle model.

    The simulator consumes approved order plans plus market-data providers and
    emits a deterministic event stream. It never calls broker submit/cancel
    methods and always reports broker_order_sent=False.
    """

    def __init__(
        self,
        *,
        quote_provider: QuoteProvider,
        l2_provider: L2Provider | None = None,
    ) -> None:
        self.quote_provider = quote_provider
        self.l2_provider = l2_provider

    def simulate(self, request: ExecutionSimulationRequest) -> ExecutionSimulationResult:
        order_plan = request.order_plan
        if order_plan.status != OrderStatus.user_approved:
            return self._blocked_result(request, reason_code="order_not_approved")
        if order_plan.intent.order_type == OrderType.market:
            return self._blocked_result(request, reason_code="market_order_disabled")

        quote_snapshot = self.quote_provider.get_quotes([order_plan.intent.symbol])
        quote = quote_snapshot.quotes.get(order_plan.intent.symbol)
        if quote is None or not quote_snapshot.data_quality.usable or quote_snapshot.provider_status.state != "available":
            return self._unavailable_result(request, quote_snapshot=quote_snapshot, reason_code="quote_unavailable")

        schedule = build_slice_schedule(order_plan, request.config)
        l2_snapshot = self._get_l2_snapshot(order_plan)
        queue_ahead = self._estimate_queue_ahead(order_plan, l2_snapshot)
        adverse_selection = self._estimate_adverse_selection_bps(order_plan, quote, l2_snapshot, queue_ahead)

        events = [
            ExecutionEvent(
                event_type="queue_estimated",
                order_plan_id=order_plan.order_plan_id,
                symbol=order_plan.intent.symbol,
                queue_ahead_quantity=queue_ahead,
                message="deterministic queue estimate",
            ),
            ExecutionEvent(
                event_type="adverse_selection_estimated",
                order_plan_id=order_plan.order_plan_id,
                symbol=order_plan.intent.symbol,
                adverse_selection_bps=adverse_selection,
                message="deterministic adverse selection proxy",
            ),
        ]

        remaining = round(order_plan.intent.quantity, 6)
        filled_quantity = 0.0
        fill_notional = 0.0
        slippage_samples: list[float] = []
        for slice_ in schedule.slices:
            events.append(
                ExecutionEvent(
                    event_type="slice_scheduled",
                    order_plan_id=order_plan.order_plan_id,
                    symbol=order_plan.intent.symbol,
                    slice_id=slice_.slice_id,
                    quantity=slice_.quantity,
                    remaining_quantity=remaining,
                )
            )
            events.append(
                ExecutionEvent(
                    event_type="broker_acceptance_simulated",
                    order_plan_id=order_plan.order_plan_id,
                    symbol=order_plan.intent.symbol,
                    slice_id=slice_.slice_id,
                    quantity=slice_.quantity,
                    message="simulated acceptance only; no broker order sent",
                )
            )

            if self._should_simulate_cancel_replace(request.config, slice_.slice_id):
                events.extend(
                    [
                        ExecutionEvent(
                            event_type="cancel_replace_requested",
                            order_plan_id=order_plan.order_plan_id,
                            symbol=order_plan.intent.symbol,
                            slice_id=slice_.slice_id,
                            quantity=slice_.quantity,
                            reason_code="simulated_price_refresh",
                        ),
                        ExecutionEvent(
                            event_type="cancel_replace_simulated",
                            order_plan_id=order_plan.order_plan_id,
                            symbol=order_plan.intent.symbol,
                            slice_id=slice_.slice_id,
                            quantity=slice_.quantity,
                            message="simulator-only cancel/replace event; no broker call path",
                        ),
                    ]
                )

            if remaining <= 0 or slice_.quantity <= 0:
                continue

            fill_probability = self._estimate_fill_probability(
                order_plan=order_plan,
                quote=quote,
                queue_ahead_quantity=queue_ahead,
                adverse_selection_bps=adverse_selection,
            )
            fill_quantity = min(remaining, round(slice_.quantity * fill_probability, 6))
            if fill_quantity <= 0:
                continue

            fill_price = self._estimate_fill_price(order_plan, quote)
            slippage_bps = self._estimate_slippage_bps(order_plan, reference_price=quote.last, fill_price=fill_price)
            remaining = round(remaining - fill_quantity, 6)
            filled_quantity = round(filled_quantity + fill_quantity, 6)
            fill_notional += fill_quantity * fill_price
            slippage_samples.append(slippage_bps)

            events.append(
                ExecutionEvent(
                    event_type="fill" if fill_quantity == slice_.quantity and remaining == 0 else "partial_fill",
                    order_plan_id=order_plan.order_plan_id,
                    symbol=order_plan.intent.symbol,
                    slice_id=slice_.slice_id,
                    quantity=slice_.quantity,
                    price=fill_price,
                    filled_quantity=fill_quantity,
                    remaining_quantity=remaining,
                    fill_probability=fill_probability,
                    slippage_bps=slippage_bps,
                )
            )

        status = ExecutionStatus.simulated
        if remaining <= 0 and filled_quantity > 0:
            status = ExecutionStatus.filled
        elif filled_quantity > 0:
            status = ExecutionStatus.partially_filled

        events.append(
            ExecutionEvent(
                event_type="completed",
                order_plan_id=order_plan.order_plan_id,
                symbol=order_plan.intent.symbol,
                filled_quantity=filled_quantity,
                remaining_quantity=remaining,
                reason_code=status.value,
            )
        )

        average_fill_price = round(fill_notional / filled_quantity, 6) if filled_quantity > 0 else None
        estimated_slippage = round(sum(slippage_samples) / len(slippage_samples), 6) if slippage_samples else 0.0
        return ExecutionSimulationResult(
            request_id=request.request_id,
            order_plan_id=order_plan.order_plan_id,
            symbol=order_plan.intent.symbol,
            status=status,
            schedule=schedule,
            events=events,
            requested_quantity=order_plan.intent.quantity,
            filled_quantity=filled_quantity,
            remaining_quantity=remaining,
            average_fill_price=average_fill_price,
            estimated_slippage_bps=estimated_slippage,
            adverse_selection_bps=adverse_selection,
            queue_ahead_quantity=queue_ahead,
            data_mode=request.config.data_mode,
            provider_state=quote_snapshot.provider_status.state,
        )

    def _blocked_result(self, request: ExecutionSimulationRequest, *, reason_code: str) -> ExecutionSimulationResult:
        return self._terminal_result(
            request,
            status=ExecutionStatus.blocked,
            event_type="blocked",
            reason_code=reason_code,
            provider_state="not_requested",
        )

    def _unavailable_result(
        self,
        request: ExecutionSimulationRequest,
        *,
        quote_snapshot: QuoteSnapshot,
        reason_code: str,
    ) -> ExecutionSimulationResult:
        return self._terminal_result(
            request,
            status=ExecutionStatus.unavailable,
            event_type="unavailable",
            reason_code=reason_code,
            provider_state=quote_snapshot.provider_status.state,
        )

    def _terminal_result(
        self,
        request: ExecutionSimulationRequest,
        *,
        status: ExecutionStatus,
        event_type: str,
        reason_code: str,
        provider_state: str,
    ) -> ExecutionSimulationResult:
        order_plan = request.order_plan
        schedule = self._empty_schedule(order_plan, request.config)
        event = ExecutionEvent(
            event_type=event_type,  # type: ignore[arg-type]
            order_plan_id=order_plan.order_plan_id,
            symbol=order_plan.intent.symbol,
            filled_quantity=0,
            remaining_quantity=order_plan.intent.quantity,
            reason_code=reason_code,
        )
        return ExecutionSimulationResult(
            request_id=request.request_id,
            order_plan_id=order_plan.order_plan_id,
            symbol=order_plan.intent.symbol,
            status=status,
            schedule=schedule,
            events=[event],
            requested_quantity=order_plan.intent.quantity,
            filled_quantity=0,
            remaining_quantity=order_plan.intent.quantity,
            data_mode=request.config.data_mode,
            provider_state=provider_state,
        )

    def _empty_schedule(self, order_plan: OrderPlan, config: ExecutionSimulatorConfig) -> SliceSchedule:
        return SliceSchedule(
            order_plan_id=order_plan.order_plan_id,
            algorithm=config.algorithm,
            slices=[],
            total_requested_quantity=order_plan.intent.quantity,
            total_scheduled_quantity=0,
            unscheduled_quantity=order_plan.intent.quantity,
        )

    def _get_l2_snapshot(self, order_plan: OrderPlan) -> L2Snapshot | None:
        if self.l2_provider is None:
            return None
        try:
            return self.l2_provider.get_l2_snapshot(order_plan.intent.symbol)
        except Exception:
            return None

    def _estimate_queue_ahead(self, order_plan: OrderPlan, l2_snapshot: L2Snapshot | None) -> float:
        limit_price = order_plan.intent.limit_price
        if l2_snapshot is None or limit_price is None:
            return round(order_plan.intent.quantity * 0.25, 6)

        levels = l2_snapshot.asks if order_plan.intent.side == "buy" else l2_snapshot.bids
        queue = 0.0
        for level in levels:
            price = float(level.get("price", 0))
            quantity = float(level.get("quantity", level.get("size", 0)))
            if order_plan.intent.side == "buy" and price <= limit_price:
                queue += quantity
            elif order_plan.intent.side == "sell" and price >= limit_price:
                queue += quantity
        return round(queue, 6)

    def _estimate_adverse_selection_bps(
        self,
        order_plan: OrderPlan,
        quote: Quote,
        l2_snapshot: L2Snapshot | None,
        queue_ahead_quantity: float,
    ) -> float:
        spread_bps = self._spread_bps(quote)
        visible_depth = queue_ahead_quantity
        if l2_snapshot is not None:
            visible_depth += sum(float(level.get("quantity", level.get("size", 0))) for level in l2_snapshot.bids)
            visible_depth += sum(float(level.get("quantity", level.get("size", 0))) for level in l2_snapshot.asks)
        depth_pressure = order_plan.intent.quantity / max(visible_depth, order_plan.intent.quantity, 1.0)
        return round((spread_bps * 0.40) + (depth_pressure * 10.0), 6)

    def _estimate_fill_probability(
        self,
        *,
        order_plan: OrderPlan,
        quote: Quote,
        queue_ahead_quantity: float,
        adverse_selection_bps: float,
    ) -> float:
        intent = order_plan.intent
        limit_price = intent.limit_price or quote.last
        crossing = (
            quote.ask is not None and limit_price >= quote.ask
            if intent.side == "buy"
            else quote.bid is not None and limit_price <= quote.bid
        )
        base = 0.88 if crossing else 0.58
        spread_penalty = min(self._spread_bps(quote) / 1_000.0, 0.12)
        queue_penalty = min(queue_ahead_quantity / max(queue_ahead_quantity + intent.quantity, 1.0) * 0.25, 0.25)
        adverse_penalty = min(adverse_selection_bps / 200.0, 0.20)
        return round(max(0.05, min(0.98, base - spread_penalty - queue_penalty - adverse_penalty)), 6)

    def _estimate_fill_price(self, order_plan: OrderPlan, quote: Quote) -> float:
        limit_price = order_plan.intent.limit_price or quote.last
        if order_plan.intent.side == "buy":
            reference = quote.ask or quote.last
            return round(min(limit_price, reference), 6)
        reference = quote.bid or quote.last
        return round(max(limit_price, reference), 6)

    def _estimate_slippage_bps(self, order_plan: OrderPlan, *, reference_price: float, fill_price: float) -> float:
        if reference_price <= 0:
            return 0.0
        if order_plan.intent.side == "buy":
            return round(max(0.0, (fill_price - reference_price) / reference_price * 10_000.0), 6)
        return round(max(0.0, (reference_price - fill_price) / reference_price * 10_000.0), 6)

    def _spread_bps(self, quote: Quote) -> float:
        if quote.bid is None or quote.ask is None:
            return 5.0
        mid = (quote.bid + quote.ask) / 2
        if mid <= 0:
            return 5.0
        return round(max(0.0, (quote.ask - quote.bid) / mid * 10_000.0), 6)

    def _should_simulate_cancel_replace(self, config: ExecutionSimulatorConfig, slice_id: int) -> bool:
        if not config.simulate_cancel_replace:
            return False
        target_slice = config.cancel_replace_at_slice or 1
        return slice_id == target_slice
