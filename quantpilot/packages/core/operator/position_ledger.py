"""Deterministic paper-position and operator-run persistence models."""

from __future__ import annotations

from datetime import date, datetime
from math import isfinite
import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from quantpilot.packages.core.schemas import Fill, HarnessModel


PaperRunStatus = Literal["started", "completed", "blocked", "failed"]
PendingLiquidationStatus = Literal[
    "prepared",
    "submitted",
    "accepted",
    "partially_filled",
    "outcome_unknown",
    "filled",
    "cancelled",
    "rejected",
    "failed",
    "reconciled",
]
OperatorCycleKind = Literal["risk_evaluation", "weekly_rebalance"]
StrategyHealthStatus = Literal[
    "active",
    "review_unavailable",
    "paused_reapproval",
    "disabled",
]
RetirementPhase = Literal[
    "none",
    "risk_first",
    "remaining",
    "awaiting_reconciliation",
    "complete",
]


_ISO_YEAR_WEEK_PATTERN = re.compile(r"^(?P<year>\d{4})-W(?P<week>\d{2})$")


def _require_aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return value


class ManagedPositionState(HarnessModel):
    """Allowlisted state required to resume management of one paper position."""

    policy_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    quantity: float = Field(gt=0, allow_inf_nan=False)
    average_entry_price: float = Field(gt=0, allow_inf_nan=False)
    atr14: float = Field(ge=0, allow_inf_nan=False)
    active_stop: float = Field(gt=0, allow_inf_nan=False)
    policy_version: int = Field(ge=1)
    opened_at: datetime
    updated_at: datetime
    reconciled_snapshot_id: str
    reconciled_at: datetime
    attribution_status: Literal["active", "conflicted"] = "active"
    attribution_conflict_reason: str | None = None
    attribution_conflicted_at: datetime | None = None
    processed_fill_ids: list[str] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)

    @field_validator(
        "policy_id",
        "strategy_id",
        "strategy_version",
        "symbol",
        "reconciled_snapshot_id",
    )
    @classmethod
    def identity_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("position identity fields must not be blank")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.upper()

    @field_validator("processed_fill_ids")
    @classmethod
    def normalize_processed_fill_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("processed fill IDs must not be blank")
        return sorted(set(normalized))

    @field_validator("attribution_conflict_reason")
    @classmethod
    def normalize_attribution_conflict_reason(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("attribution conflict reason must not be blank")
        return normalized

    @field_validator(
        "opened_at",
        "updated_at",
        "reconciled_at",
        "attribution_conflicted_at",
    )
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_timestamp(value)

    @model_validator(mode="after")
    def update_cannot_precede_open(self) -> "ManagedPositionState":
        if self.updated_at < self.opened_at:
            raise ValueError("updated_at cannot precede opened_at")
        if self.attribution_status == "active":
            if (
                self.attribution_conflict_reason is not None
                or self.attribution_conflicted_at is not None
            ):
                raise ValueError("active attribution cannot retain conflict evidence")
            if self.reconciled_at < self.updated_at:
                raise ValueError("reconciled_at cannot precede updated_at")
        else:
            if (
                self.attribution_conflict_reason is None
                or self.attribution_conflicted_at is None
            ):
                raise ValueError("conflicted attribution requires durable evidence")
            if self.attribution_conflicted_at != self.updated_at:
                raise ValueError("attribution conflict time must equal updated_at")
            if self.attribution_conflicted_at < self.reconciled_at:
                raise ValueError("attribution conflict cannot precede reconciliation")
        return self

    @property
    def storage_key(self) -> tuple[str, str, str, str]:
        return self.policy_id, self.strategy_id, self.strategy_version, self.symbol


class ManagedPositionBinding(HarnessModel):
    """Trusted policy/strategy attribution derived from persisted position state."""

    policy_id: str
    policy_version: int = Field(ge=1)
    strategy_id: str
    strategy_version: str
    symbol: str
    quantity: float = Field(gt=0, allow_inf_nan=False)
    updated_at: datetime
    reconciled_snapshot_id: str
    reconciled_at: datetime

    @field_validator(
        "policy_id",
        "strategy_id",
        "strategy_version",
        "symbol",
        "reconciled_snapshot_id",
    )
    @classmethod
    def identity_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("managed-position binding fields must not be blank")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.upper()

    @field_validator("updated_at", "reconciled_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)

    @classmethod
    def from_position(
        cls,
        position: ManagedPositionState,
    ) -> "ManagedPositionBinding":
        if position.attribution_status != "active":
            raise ValueError("conflicted managed position cannot authorize an order")
        return cls(
            policy_id=position.policy_id,
            policy_version=position.policy_version,
            strategy_id=position.strategy_id,
            strategy_version=position.strategy_version,
            symbol=position.symbol,
            quantity=position.quantity,
            updated_at=position.updated_at,
            reconciled_snapshot_id=position.reconciled_snapshot_id,
            reconciled_at=position.reconciled_at,
        )


class PaperRunCheckpoint(HarnessModel):
    """Minimal, secret-free checkpoint for idempotent paper operator cycles."""

    run_id: str
    idempotency_key: str
    policy_id: str
    user_id: str
    policy_version: int = Field(ge=1)
    run_mode: Literal["dry_run", "mock_submit", "paper_submit"]
    requested_at: datetime
    request_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: PaperRunStatus
    data_mode: Literal["fixture", "paper_trading"] = "paper_trading"
    started_at: datetime
    updated_at: datetime
    result_payload: dict[str, Any] | None = None

    @field_validator("run_id", "idempotency_key", "policy_id", "user_id")
    @classmethod
    def identity_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("run identity fields must not be blank")
        return normalized

    @field_validator("requested_at", "started_at", "updated_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)

    @model_validator(mode="after")
    def update_cannot_precede_start(self) -> "PaperRunCheckpoint":
        if self.updated_at < self.started_at:
            raise ValueError("updated_at cannot precede started_at")
        return self


class PendingLiquidationCheckpoint(HarnessModel):
    """Secret-free recovery record written before a broker liquidation call."""

    order_plan_id: str
    policy_id: str
    policy_version: int = Field(ge=1)
    strategy_id: str
    strategy_version: str
    symbol: str
    purpose: Literal["protective_exit", "strategy_retirement"]
    idempotency_key: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    quantity_before: float = Field(gt=0, allow_inf_nan=False)
    quantity_requested: float = Field(gt=0, allow_inf_nan=False)
    expected_quantity_after: float = Field(ge=0, allow_inf_nan=False)
    account_quantity_before: float = Field(gt=0, allow_inf_nan=False)
    expected_account_quantity_after: float = Field(ge=0, allow_inf_nan=False)
    cumulative_filled_quantity: float = Field(default=0, ge=0, allow_inf_nan=False)
    fill_ids: list[str] = Field(default_factory=list)
    fill_evidence: list[Fill] = Field(default_factory=list)
    limit_price: float = Field(gt=0, allow_inf_nan=False)
    quote_as_of: datetime
    reconciled_snapshot_id: str
    status: PendingLiquidationStatus = "prepared"
    broker_submission_attempted: bool = False
    risk_check_id: str | None = None
    broker_order_id: str | None = None
    last_error_code: str | None = None
    created_at: datetime
    updated_at: datetime
    revision: int = Field(default=0, ge=0)

    @field_validator(
        "order_plan_id",
        "policy_id",
        "strategy_id",
        "strategy_version",
        "symbol",
        "idempotency_key",
        "reconciled_snapshot_id",
    )
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("pending-liquidation identity fields must not be blank")
        return normalized

    @field_validator("risk_check_id", "broker_order_id", "last_error_code")
    @classmethod
    def optional_text_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("pending-liquidation optional identifiers must not be blank")
        return normalized

    @field_validator("fill_ids")
    @classmethod
    def normalize_pending_fill_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("pending-liquidation fill IDs must not be blank")
        return sorted(set(normalized))

    @field_validator("fill_evidence")
    @classmethod
    def normalize_pending_fill_evidence(cls, value: list[Fill]) -> list[Fill]:
        fill_ids = [item.fill_id for item in value]
        if len(fill_ids) != len(set(fill_ids)):
            raise ValueError("pending-liquidation fill evidence contains duplicate IDs")
        return sorted(value, key=lambda item: item.fill_id)

    @field_validator("symbol")
    @classmethod
    def normalize_pending_symbol(cls, value: str) -> str:
        return value.upper()

    @field_validator("quote_as_of", "created_at", "updated_at")
    @classmethod
    def pending_timestamps_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)

    @model_validator(mode="after")
    def pending_quantities_and_time_must_be_consistent(
        self,
    ) -> "PendingLiquidationCheckpoint":
        if self.quantity_requested > self.quantity_before + 0.000001:
            raise ValueError("pending liquidation cannot exceed attributed quantity")
        expected = self.quantity_before - self.quantity_requested
        if abs(expected - self.expected_quantity_after) > 0.000001:
            raise ValueError("expected quantity must equal quantity before minus request")
        if self.quantity_before > self.account_quantity_before + 0.000001:
            raise ValueError("attributed quantity cannot exceed reconciled account quantity")
        expected_account = self.account_quantity_before - self.quantity_requested
        if abs(expected_account - self.expected_account_quantity_after) > 0.000001:
            raise ValueError(
                "expected account quantity must equal account quantity before minus request"
            )
        if self.cumulative_filled_quantity > self.quantity_requested + 0.000001:
            raise ValueError("cumulative fills cannot exceed the liquidation request")
        if self.status == "filled" and abs(
            self.cumulative_filled_quantity - self.quantity_requested
        ) > 0.000001:
            raise ValueError("filled liquidation must account for the full requested quantity")
        broker_states = {
            "submitted",
            "accepted",
            "partially_filled",
            "outcome_unknown",
            "filled",
            "cancelled",
            "rejected",
        }
        if self.status in broker_states and not self.broker_submission_attempted:
            raise ValueError("broker-state checkpoints require a submission attempt")
        if self.broker_submission_attempted and self.risk_check_id is None:
            raise ValueError("broker submission attempts require the final risk check ID")
        if self.risk_check_id is not None and not self.broker_submission_attempted:
            raise ValueError("final risk check ID requires a broker submission attempt")
        if self.status == "prepared" and self.broker_submission_attempted:
            raise ValueError("prepared checkpoints cannot claim a broker submission")
        if (
            self.broker_order_id is not None or self.fill_evidence
        ) and not self.broker_submission_attempted:
            raise ValueError("broker evidence requires a submission attempt")
        evidence_ids = [item.fill_id for item in self.fill_evidence]
        if sorted(self.fill_ids) != evidence_ids:
            raise ValueError("fill IDs must exactly match durable fill evidence")
        evidence_quantity = sum(item.quantity for item in self.fill_evidence)
        if abs(evidence_quantity - self.cumulative_filled_quantity) > 0.000001:
            raise ValueError("cumulative fills must equal durable fill-evidence quantity")
        for fill in self.fill_evidence:
            if not all(
                isfinite(value)
                for value in (fill.quantity, fill.price, fill.notional)
            ):
                raise ValueError("fill evidence values must be finite")
            if (
                fill.order_plan_id != self.order_plan_id
                or fill.symbol.strip().upper() != self.symbol
                or self.broker_order_id is None
                or fill.broker_order_id != self.broker_order_id
            ):
                raise ValueError("fill evidence must match the pending liquidation")
            if fill.filled_at.tzinfo is None or fill.filled_at.utcoffset() is None:
                raise ValueError("fill-evidence timestamps must include a UTC offset")
            if fill.filled_at > self.updated_at:
                raise ValueError("fill evidence cannot occur after the checkpoint update")
            expected_fill_notional = fill.quantity * fill.price
            if abs(expected_fill_notional - fill.notional) > max(
                0.01,
                fill.notional * 0.000001,
            ):
                raise ValueError(
                    "fill-evidence notional must equal quantity times price"
                )
        if self.updated_at < self.created_at:
            raise ValueError("pending-liquidation updated_at cannot precede created_at")
        if self.quote_as_of > self.created_at:
            raise ValueError("pending-liquidation quote cannot be in the future")
        return self


class OperatorCycleClaim(HarnessModel):
    """Atomic minute/week claim preventing concurrent duplicate operator cycles."""

    policy_id: str
    strategy_id: str
    strategy_version: str
    cycle_kind: OperatorCycleKind
    bucket: str
    claimed_at: datetime
    lease_expires_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator("policy_id", "strategy_id", "strategy_version", "bucket")
    @classmethod
    def cycle_identity_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("operator-cycle claim fields must not be blank")
        return normalized

    @field_validator("claimed_at", "lease_expires_at", "completed_at")
    @classmethod
    def claim_timestamps_must_be_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return None if value is None else _require_aware_timestamp(value)

    @model_validator(mode="after")
    def bucket_must_match_cycle_kind(self) -> "OperatorCycleClaim":
        if self.cycle_kind == "risk_evaluation":
            try:
                parsed = datetime.strptime(self.bucket, "%Y-%m-%dT%H:%MZ")
            except ValueError as exc:
                raise ValueError("risk cycle bucket must use YYYY-MM-DDTHH:MMZ") from exc
            if parsed.second != 0:
                raise ValueError("risk cycle bucket must identify one minute")
        else:
            match = _ISO_YEAR_WEEK_PATTERN.fullmatch(self.bucket)
            if match is None:
                raise ValueError("weekly rebalance bucket must use YYYY-Www")
            try:
                date.fromisocalendar(
                    int(match.group("year")),
                    int(match.group("week")),
                    1,
                )
            except ValueError as exc:
                raise ValueError("weekly rebalance bucket must be a valid ISO week") from exc
        if self.cycle_kind == "risk_evaluation" and self.lease_expires_at is not None:
            raise ValueError("risk cycle claims must not use a lease")
        if self.cycle_kind == "risk_evaluation" and self.completed_at is not None:
            raise ValueError("risk cycle claims must not use completion state")
        if self.cycle_kind == "weekly_rebalance":
            if (
                self.lease_expires_at is None
                or self.lease_expires_at <= self.claimed_at
            ):
                raise ValueError(
                    "weekly rebalance claims require a future lease expiry"
                )
            if self.completed_at is not None and not (
                self.claimed_at <= self.completed_at <= self.lease_expires_at
            ):
                raise ValueError(
                    "weekly completion must occur during the owned lease"
                )
        return self

    @property
    def storage_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.policy_id,
            self.strategy_id,
            self.strategy_version,
            self.cycle_kind,
            self.bucket,
        )


class OperatorSafetyState(HarnessModel):
    """Durable pause and broker-health state for one policy operator."""

    policy_id: str
    autopilot_paused: bool = False
    broker_healthy: bool = True
    last_blocked_reason: str | None = None
    updated_at: datetime
    revision: int = Field(default=0, ge=0)

    @field_validator("policy_id")
    @classmethod
    def safety_policy_id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("operator safety policy ID must not be blank")
        return normalized

    @field_validator("updated_at")
    @classmethod
    def safety_timestamp_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)


class StrategyOperatorState(HarnessModel):
    """Secret-free, restart-safe strategy health and retirement progress."""

    policy_id: str
    strategy_id: str
    strategy_version: str
    health_status: StrategyHealthStatus = "active"
    reason_codes: list[str] = Field(default_factory=list)
    performance_record_id: str | None = None
    retirement_phase: RetirementPhase = "none"
    pending_order_plan_ids: list[str] = Field(default_factory=list)
    last_risk_evaluated_at: datetime | None = None
    last_rebalance_session: str | None = None
    updated_at: datetime
    revision: int = Field(default=0, ge=0)

    @field_validator("policy_id", "strategy_id", "strategy_version")
    @classmethod
    def identity_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("strategy operator identity fields must not be blank")
        return normalized

    @field_validator("reason_codes", "pending_order_plan_ids")
    @classmethod
    def normalize_identifiers(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("strategy operator identifiers must not be blank")
        return sorted(set(normalized))

    @field_validator("performance_record_id")
    @classmethod
    def performance_record_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("performance_record_id must not be blank")
        return normalized

    @field_validator("last_risk_evaluated_at", "updated_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_aware_timestamp(value)

    @field_validator("last_rebalance_session")
    @classmethod
    def rebalance_session_must_be_iso_year_week(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        match = _ISO_YEAR_WEEK_PATTERN.fullmatch(normalized)
        if match is None:
            raise ValueError("last_rebalance_session must use canonical YYYY-Www format")
        year = int(match.group("year"))
        week = int(match.group("week"))
        try:
            date.fromisocalendar(year, week, 1)
        except ValueError as exc:
            raise ValueError("last_rebalance_session must be a valid ISO year-week") from exc
        return f"{year:04d}-W{week:02d}"

    @model_validator(mode="after")
    def cadence_and_retirement_state_must_be_consistent(self) -> "StrategyOperatorState":
        if (
            self.last_risk_evaluated_at is not None
            and self.last_risk_evaluated_at > self.updated_at
        ):
            raise ValueError("last_risk_evaluated_at cannot be later than updated_at")
        if self.retirement_phase == "awaiting_reconciliation" and not self.pending_order_plan_ids:
            raise ValueError("awaiting_reconciliation requires pending order plans")
        if self.pending_order_plan_ids and self.retirement_phase != "awaiting_reconciliation":
            raise ValueError("pending order plans require awaiting_reconciliation phase")
        if self.retirement_phase == "complete" and self.pending_order_plan_ids:
            raise ValueError("complete retirement cannot retain pending order plans")
        return self

    @property
    def storage_key(self) -> tuple[str, str, str]:
        return self.policy_id, self.strategy_id, self.strategy_version
