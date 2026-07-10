"""Pure best-bid limit-sell decisions for protective exits and retirement."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite, isnan
from typing import Literal

from pydantic import Field, StrictInt, field_validator

from quantpilot.packages.core.schemas import HarnessModel


LiquidationPurpose = Literal["protective_exit", "strategy_retirement"]
LiquidationDecisionStatus = Literal["blocked", "ready"]

_ROUND_DECIMALS = 6


class MarketableLimitLiquidationInput(HarnessModel):
    """Reconciled position state and quote evidence for one sell decision."""

    purpose: LiquidationPurpose
    policy_id: str
    policy_version: StrictInt = Field(ge=1)
    strategy_id: str
    strategy_version: str
    symbol: str
    quantity_held: StrictInt
    quantity_requested: StrictInt
    current_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    best_bid: float | None = None
    quote_as_of: datetime
    evaluated_at: datetime
    max_quote_age_seconds: StrictInt = Field(default=30, gt=0)
    managed_position_updated_at: datetime
    reconciled_snapshot_id: str
    reconciled_at: datetime
    reason_code: str

    @field_validator(
        "policy_id",
        "strategy_id",
        "strategy_version",
        "symbol",
        "reconciled_snapshot_id",
        "reason_code",
    )
    @classmethod
    def identity_fields_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identity and reason fields must not be blank")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.upper()


class MarketableLimitLiquidationDecision(HarnessModel):
    """Broker-free decision that is executable only when ``status`` is ready."""

    status: LiquidationDecisionStatus
    purpose: LiquidationPurpose
    policy_id: str
    policy_version: StrictInt = Field(ge=1)
    strategy_id: str
    strategy_version: str
    symbol: str
    side: Literal["sell"] = "sell"
    order_type: Literal["limit"] = "limit"
    quantity_held: StrictInt
    quantity_requested: StrictInt
    quantity_to_sell: StrictInt = Field(ge=0)
    current_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    limit_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    notional: float = Field(ge=0, allow_inf_nan=False)
    target_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    quote_age_seconds: float | None = Field(default=None, allow_inf_nan=False)
    reconciled_snapshot_id: str
    idempotency_key: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reason_codes: list[str] = Field(default_factory=list)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_timestamp(value: datetime) -> str:
    if _is_aware(value):
        value = value.astimezone(timezone.utc)
        return value.isoformat(timespec="microseconds")
    return f"naive:{value.isoformat(timespec='microseconds')}"


def _canonical_float(value: float | None) -> str | None:
    if value is None:
        return None
    if isnan(value):
        return "nan"
    if not isfinite(value):
        return "+inf" if value > 0 else "-inf"
    return format(value, ".17g")


def _idempotency_key(request: MarketableLimitLiquidationInput) -> str:
    payload = {
        "best_bid": _canonical_float(request.best_bid),
        "current_weight": _canonical_float(request.current_weight),
        "evaluated_at": _canonical_timestamp(request.evaluated_at),
        "managed_position_updated_at": _canonical_timestamp(
            request.managed_position_updated_at
        ),
        "reconciled_snapshot_id": request.reconciled_snapshot_id,
        "reconciled_at": _canonical_timestamp(request.reconciled_at),
        "max_quote_age_seconds": request.max_quote_age_seconds,
        "policy_id": request.policy_id,
        "policy_version": request.policy_version,
        "purpose": request.purpose,
        "quantity_held": request.quantity_held,
        "quantity_requested": request.quantity_requested,
        "quote_as_of": _canonical_timestamp(request.quote_as_of),
        "reason_code": request.reason_code,
        "strategy_id": request.strategy_id,
        "strategy_version": request.strategy_version,
        "symbol": request.symbol,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def build_marketable_limit_liquidation_decision(
    request: MarketableLimitLiquidationInput,
) -> MarketableLimitLiquidationDecision:
    """Build a deterministic sell decision without a broker or runtime clock."""

    reason_codes = [request.reason_code]
    blocking_reasons: list[str] = []

    if request.quantity_held <= 0:
        blocking_reasons.append("quantity_held_invalid")
    if request.quantity_requested <= 0:
        blocking_reasons.append("quantity_requested_invalid")

    if request.best_bid is None:
        blocking_reasons.append("best_bid_missing")
    elif not isfinite(request.best_bid):
        blocking_reasons.append("best_bid_not_finite")
    elif request.best_bid <= 0:
        blocking_reasons.append("best_bid_nonpositive")

    quote_age_seconds: float | None = None
    if not (_is_aware(request.quote_as_of) and _is_aware(request.evaluated_at)):
        blocking_reasons.append("quote_timestamp_naive")
    else:
        quote_age_seconds = (
            request.evaluated_at - request.quote_as_of
        ).total_seconds()
        if quote_age_seconds < 0:
            blocking_reasons.append("quote_future")
        elif quote_age_seconds > request.max_quote_age_seconds:
            blocking_reasons.append("quote_stale")

    if not (
        _is_aware(request.managed_position_updated_at)
        and _is_aware(request.reconciled_at)
        and _is_aware(request.evaluated_at)
    ):
        blocking_reasons.append("managed_position_timestamp_naive")
    elif request.managed_position_updated_at > request.reconciled_at:
        blocking_reasons.append("managed_position_after_reconciliation")
    elif request.reconciled_at > request.evaluated_at:
        blocking_reasons.append("reconciliation_future")

    idempotency_key = _idempotency_key(request)

    def decision(
        *,
        status: LiquidationDecisionStatus,
        quantity_to_sell: int,
        limit_price: float | None,
        notional: float,
        target_weight: float,
        codes: list[str],
    ) -> MarketableLimitLiquidationDecision:
        return MarketableLimitLiquidationDecision(
            status=status,
            purpose=request.purpose,
            policy_id=request.policy_id,
            policy_version=request.policy_version,
            strategy_id=request.strategy_id,
            strategy_version=request.strategy_version,
            symbol=request.symbol,
            quantity_held=request.quantity_held,
            quantity_requested=request.quantity_requested,
            quantity_to_sell=quantity_to_sell,
            current_weight=request.current_weight,
            limit_price=limit_price,
            notional=notional,
            target_weight=target_weight,
            quote_age_seconds=(
                None
                if quote_age_seconds is None
                else round(quote_age_seconds, _ROUND_DECIMALS)
            ),
            reconciled_snapshot_id=request.reconciled_snapshot_id,
            idempotency_key=idempotency_key,
            reason_codes=codes,
        )

    if blocking_reasons:
        return decision(
            status="blocked",
            quantity_to_sell=0,
            limit_price=None,
            notional=0.0,
            target_weight=round(request.current_weight, _ROUND_DECIMALS),
            codes=[*reason_codes, *blocking_reasons],
        )

    quantity_to_sell = min(request.quantity_held, request.quantity_requested)
    if request.quantity_requested > request.quantity_held:
        reason_codes.append("quantity_capped_to_holding")

    assert request.best_bid is not None
    notional = request.best_bid * quantity_to_sell
    if not isfinite(notional):
        return decision(
            status="blocked",
            quantity_to_sell=0,
            limit_price=None,
            notional=0.0,
            target_weight=round(request.current_weight, _ROUND_DECIMALS),
            codes=[*reason_codes, "notional_not_finite"],
        )

    remaining_fraction = (
        request.quantity_held - quantity_to_sell
    ) / request.quantity_held
    reason_codes.append("marketable_limit_sell_ready")
    return decision(
        status="ready",
        quantity_to_sell=quantity_to_sell,
        limit_price=request.best_bid,
        notional=round(notional, _ROUND_DECIMALS),
        target_weight=round(
            request.current_weight * remaining_fraction,
            _ROUND_DECIMALS,
        ),
        codes=reason_codes,
    )
