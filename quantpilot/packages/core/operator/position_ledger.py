"""Deterministic paper-position and operator-run persistence models."""

from __future__ import annotations

from datetime import date, datetime
from math import isclose, isfinite
import re
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from quantpilot.packages.core.schemas import (
    Fill,
    HarnessModel,
    OrderPlan,
    OrderStatus,
    new_id,
)


PaperRunStatus = Literal["started", "completed", "blocked", "failed"]
StateStoreDataMode = Literal["fixture", "paper_trading"]
StateStoreBrokerEnvironment = Literal["fixture_mock", "kis_paper"]
PaperExecutionSessionStatus = Literal["active", "closed", "abandoned"]
PaperDispatchStatus = Literal[
    "prepared",
    "dispatch_claimed",
    "outcome_unknown",
    "accepted",
    "partially_filled",
    "filled",
    "rejected",
    "cancelled",
    "expired_pre_dispatch",
    "failed_pre_dispatch",
]
PaperDispatchReconciliationStatus = Literal["pending", "reconciled", "blocked"]
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
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_KST = ZoneInfo("Asia/Seoul")


def _require_aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return value


class StateStoreProvenance(HarnessModel):
    """Immutable identity for one fixture or KIS-paper SQLite state store."""

    store_id: str = Field(default_factory=lambda: new_id("store"))
    schema_version: int = Field(ge=1)
    data_mode: StateStoreDataMode
    broker_environment: StateStoreBrokerEnvironment
    account_scope_fingerprint: str | None = None
    created_at: datetime

    @field_validator("store_id")
    @classmethod
    def store_id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("state-store ID must not be blank")
        return normalized

    @field_validator("account_scope_fingerprint")
    @classmethod
    def account_scope_must_be_opaque_sha256(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if _SHA256_PATTERN.fullmatch(normalized) is None:
            raise ValueError(
                "account scope fingerprint must be an opaque sha256 digest"
            )
        return normalized

    @field_validator("created_at")
    @classmethod
    def provenance_timestamp_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)

    @model_validator(mode="after")
    def mode_environment_and_account_must_match(self) -> "StateStoreProvenance":
        if self.data_mode == "fixture":
            if self.broker_environment != "fixture_mock":
                raise ValueError("fixture stores require fixture_mock environment")
            if self.account_scope_fingerprint is not None:
                raise ValueError("fixture stores cannot bind a paper account")
        else:
            if self.broker_environment != "kis_paper":
                raise ValueError("paper stores require kis_paper environment")
            if self.account_scope_fingerprint is None:
                raise ValueError("paper stores require an opaque account binding")
        return self


class PaperPortfolioLossBaseline(HarnessModel):
    """Immutable daily loss denominator bound to one KIS paper account."""

    store_id: str
    data_mode: Literal["paper_trading"] = "paper_trading"
    broker_environment: Literal["kis_paper"] = "kis_paper"
    account_scope_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    business_date: date
    month_key: str = Field(pattern=r"^\d{4}-\d{2}$")
    prior_close_equity: float = Field(gt=0, allow_inf_nan=False)
    month_start_equity: float = Field(gt=0, allow_inf_nan=False)
    source: Literal["manual_confirmed", "prior_session_close"]
    source_business_date: date
    captured_at: datetime
    confirmed_at: datetime | None = None
    revision: Literal[0] = 0

    @field_validator("store_id")
    @classmethod
    def baseline_store_id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("paper loss-baseline store ID must not be blank")
        return normalized

    @field_validator("captured_at", "confirmed_at")
    @classmethod
    def baseline_timestamps_must_be_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return None if value is None else _require_aware_timestamp(value)

    @model_validator(mode="after")
    def date_source_and_confirmation_must_be_consistent(
        self,
    ) -> "PaperPortfolioLossBaseline":
        expected_month = self.business_date.strftime("%Y-%m")
        if self.month_key != expected_month:
            raise ValueError("paper loss-baseline month key must match business date")
        if self.source_business_date >= self.business_date:
            raise ValueError(
                "paper loss-baseline source business date must be strictly earlier"
            )
        if self.captured_at.astimezone(_KST).date() != self.business_date:
            raise ValueError(
                "paper loss-baseline capture must occur on its KST business date"
            )
        if self.source == "manual_confirmed":
            if self.confirmed_at is None:
                raise ValueError("manual paper loss baseline requires confirmation")
            if self.confirmed_at < self.captured_at:
                raise ValueError(
                    "paper loss-baseline confirmation cannot precede capture"
                )
            if self.confirmed_at.astimezone(_KST).date() != self.business_date:
                raise ValueError(
                    "paper loss-baseline confirmation must occur on its KST business date"
                )
        elif self.confirmed_at is not None:
            raise ValueError(
                "prior-session loss baseline cannot claim manual confirmation"
            )
        if (
            self.source == "prior_session_close"
            and self.source_business_date.strftime("%Y-%m") != self.month_key
        ):
            raise ValueError(
                "month rollover requires a manually confirmed loss baseline"
            )
        return self


class PaperExecutionSession(HarnessModel):
    """Leased and fenced authority for one KIS paper process session."""

    session_id: str = Field(default_factory=lambda: new_id("psess"))
    store_id: str
    data_mode: Literal["paper_trading"] = "paper_trading"
    broker_environment: Literal["kis_paper"] = "kis_paper"
    account_scope_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    fencing_token: int = Field(ge=1)
    status: PaperExecutionSessionStatus = "active"
    started_at: datetime
    lease_expires_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
    revision: int = Field(default=0, ge=0)

    @field_validator("session_id", "store_id")
    @classmethod
    def session_identity_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("paper-session identity must not be blank")
        return normalized

    @field_validator("started_at", "lease_expires_at", "updated_at", "ended_at")
    @classmethod
    def session_timestamps_must_be_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return None if value is None else _require_aware_timestamp(value)

    @model_validator(mode="after")
    def lease_and_terminal_state_must_be_consistent(self) -> "PaperExecutionSession":
        if self.lease_expires_at <= self.started_at:
            raise ValueError("paper-session lease must expire after it starts")
        if self.updated_at < self.started_at:
            raise ValueError("paper-session update cannot precede its start")
        if self.status == "active":
            if self.ended_at is not None:
                raise ValueError("active paper sessions cannot have an end time")
            if self.updated_at >= self.lease_expires_at:
                raise ValueError("active paper-session update must remain within its lease")
        else:
            if self.ended_at is None:
                raise ValueError("terminal paper sessions require an end time")
            if self.ended_at < self.started_at or self.updated_at != self.ended_at:
                raise ValueError(
                    "paper-session terminal update must equal its valid end time"
                )
        return self


class PaperDispatchFillEvidence(HarnessModel):
    """Allowlisted broker fill evidence without credentials or account identifiers."""

    broker_fill_reference: str
    broker_order_id: str
    broker_order_reference: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0, allow_inf_nan=False)
    price: float = Field(gt=0, allow_inf_nan=False)
    notional: float = Field(gt=0, allow_inf_nan=False)
    evidence_at: datetime
    time_basis: Literal[
        "broker_execution",
        "broker_daily_aggregate_first_observed",
    ]

    @field_validator(
        "broker_fill_reference",
        "broker_order_id",
        "broker_order_reference",
        "symbol",
    )
    @classmethod
    def fill_identity_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("paper-dispatch fill identity must not be blank")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_fill_symbol(cls, value: str) -> str:
        return value.upper()

    @field_validator("evidence_at")
    @classmethod
    def fill_timestamp_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)

    @model_validator(mode="after")
    def fill_notional_must_match(self) -> "PaperDispatchFillEvidence":
        if not isclose(
            self.notional,
            self.quantity * self.price,
            rel_tol=0.000001,
            abs_tol=0.01,
        ):
            raise ValueError("paper-dispatch fill notional must equal quantity times price")
        return self


class PaperOrderDispatch(HarnessModel):
    """Process-kill-safe, secret-free evidence for one external paper order."""

    order_plan_id: str
    broker_order_id: str = Field(default_factory=lambda: new_id("bord"))
    run_id: str
    idempotency_key: str
    request_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    order_plan_payload: dict[str, Any] | None = None
    policy_id: str
    policy_version: int = Field(ge=1)
    user_id: str
    strategy_id: str
    strategy_version: str
    purpose: Literal["rebalance", "protective_exit", "strategy_retirement"]
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["limit"] = "limit"
    quantity: float = Field(gt=0, allow_inf_nan=False)
    limit_price: float = Field(gt=0, allow_inf_nan=False)
    quote_as_of: datetime
    quote_last: float = Field(gt=0, allow_inf_nan=False)
    quote_bid: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    quote_ask: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    quote_reference_basis: Literal["l2_midpoint", "best_bid", "best_ask"]
    risk_check_id: str
    risk_check_expires_at: datetime
    submission_evidence_expires_at: datetime
    reconciled_snapshot_id: str
    reconciled_snapshot_at: datetime
    snapshot_cash: float = Field(ge=0, allow_inf_nan=False)
    snapshot_equity: float = Field(gt=0, allow_inf_nan=False)
    snapshot_symbol_quantity: float = Field(ge=0, allow_inf_nan=False)
    snapshot_symbol_orderable_quantity: float = Field(ge=0, allow_inf_nan=False)
    snapshot_daily_loss_ratio: float = Field(ge=-1, allow_inf_nan=False)
    snapshot_monthly_loss_ratio: float = Field(ge=-1, allow_inf_nan=False)
    broker_orderable_cash: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    broker_orderable_buy_quantity: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    entry_atr14: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    store_id: str
    session_id: str
    fencing_token: int = Field(ge=1)
    data_mode: Literal["paper_trading"] = "paper_trading"
    broker_environment: Literal["kis_paper"] = "kis_paper"
    account_scope_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: PaperDispatchStatus = "prepared"
    reconciliation_status: PaperDispatchReconciliationStatus = "pending"
    attempt_count: int = Field(default=0, ge=0, le=1)
    dispatch_claimed_at: datetime | None = None
    broker_business_date: date | None = None
    broker_order_reference: str | None = None
    broker_forwarding_order_org_number: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9]{1,16}$",
    )
    broker_order_branch_number: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9]{1,16}$",
    )
    broker_order_time: str | None = Field(default=None, pattern=r"^\d{6}$")
    cumulative_filled_quantity: float = Field(default=0, ge=0, allow_inf_nan=False)
    fill_evidence: list[PaperDispatchFillEvidence] = Field(default_factory=list)
    last_error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    prepared_at: datetime
    updated_at: datetime
    reconciled_at: datetime | None = None
    revision: int = Field(default=0, ge=0)

    @field_validator(
        "order_plan_id",
        "broker_order_id",
        "run_id",
        "idempotency_key",
        "policy_id",
        "user_id",
        "strategy_id",
        "strategy_version",
        "symbol",
        "risk_check_id",
        "reconciled_snapshot_id",
        "store_id",
        "session_id",
    )
    @classmethod
    def dispatch_identity_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("paper-dispatch identity fields must not be blank")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_dispatch_symbol(cls, value: str) -> str:
        return value.upper()

    @field_validator("broker_order_reference")
    @classmethod
    def optional_broker_reference_must_not_be_blank(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("broker order reference must not be blank")
        return normalized

    @field_validator("broker_order_time")
    @classmethod
    def broker_order_time_must_be_valid_hhmmss(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        try:
            datetime.strptime(value, "%H%M%S")
        except ValueError as exc:
            raise ValueError("broker order time must be valid HHMMSS") from exc
        return value

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_broker_org_number(cls, value: Any) -> Any:
        """Treat the legacy organization field only as POST forwarding evidence."""

        if not isinstance(value, dict) or "broker_order_org_number" not in value:
            return value
        migrated = dict(value)
        legacy = migrated.pop("broker_order_org_number")
        forwarding = migrated.get("broker_forwarding_order_org_number")
        if forwarding is not None and legacy is not None and forwarding != legacy:
            raise ValueError(
                "legacy broker organization conflicts with forwarding evidence"
            )
        if forwarding is None:
            migrated["broker_forwarding_order_org_number"] = legacy
        return migrated

    @field_validator(
        "quote_as_of",
        "risk_check_expires_at",
        "submission_evidence_expires_at",
        "reconciled_snapshot_at",
        "dispatch_claimed_at",
        "prepared_at",
        "updated_at",
        "reconciled_at",
    )
    @classmethod
    def dispatch_timestamps_must_be_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return None if value is None else _require_aware_timestamp(value)

    @field_validator("fill_evidence")
    @classmethod
    def normalize_dispatch_fills(
        cls,
        value: list[PaperDispatchFillEvidence],
    ) -> list[PaperDispatchFillEvidence]:
        references = [item.broker_fill_reference for item in value]
        if len(references) != len(set(references)):
            raise ValueError("paper-dispatch fill references must be unique")
        return sorted(value, key=lambda item: item.broker_fill_reference)

    @model_validator(mode="after")
    def dispatch_state_and_evidence_must_be_consistent(self) -> "PaperOrderDispatch":
        if self.updated_at < self.prepared_at:
            raise ValueError("paper-dispatch update cannot precede preparation")
        if self.quote_as_of > self.prepared_at:
            raise ValueError("paper-dispatch quote cannot be in the future")
        if self.reconciled_snapshot_at > self.prepared_at:
            raise ValueError("paper-dispatch snapshot cannot be in the future")
        if self.risk_check_expires_at <= self.prepared_at:
            raise ValueError("paper-dispatch final risk check must be fresh")
        if self.submission_evidence_expires_at <= self.prepared_at:
            raise ValueError("paper-dispatch submission evidence must be fresh")
        if self.submission_evidence_expires_at > self.risk_check_expires_at:
            raise ValueError(
                "submission evidence expiry cannot exceed final risk expiry"
            )
        if self.quote_bid is not None and self.quote_ask is not None:
            if self.quote_bid > self.quote_ask:
                raise ValueError("paper-dispatch bid cannot exceed ask")
        if self.quote_reference_basis == "l2_midpoint":
            if (
                self.quote_bid is None
                or self.quote_ask is None
                or not isclose(
                    self.quote_last,
                    (self.quote_bid + self.quote_ask) / 2,
                    rel_tol=0,
                    abs_tol=0.000001,
                )
            ):
                raise ValueError(
                    "l2_midpoint reference requires matching bid/ask evidence"
                )
        elif self.quote_reference_basis == "best_bid":
            if self.quote_bid is None or not isclose(
                self.quote_last,
                self.quote_bid,
                rel_tol=0,
                abs_tol=0.000001,
            ):
                raise ValueError("best_bid reference must equal durable bid evidence")
        elif self.quote_ask is None or not isclose(
            self.quote_last,
            self.quote_ask,
            rel_tol=0,
            abs_tol=0.000001,
        ):
            raise ValueError("best_ask reference must equal durable ask evidence")
        if self.side == "buy" and self.quote_ask is None:
            raise ValueError("paper-dispatch buys require ask evidence")
        if self.side == "sell" and self.quote_bid is None:
            raise ValueError("paper-dispatch sells require bid evidence")
        if self.side == "buy":
            if (
                self.broker_orderable_cash is None
                or self.broker_orderable_buy_quantity is None
            ):
                raise ValueError(
                    "paper-dispatch buys require broker orderability evidence"
                )
            if self.quantity > self.broker_orderable_buy_quantity + 0.000001:
                raise ValueError(
                    "paper-dispatch buy exceeds broker-orderable quantity"
                )
            if (
                self.quantity * self.limit_price
                > self.broker_orderable_cash + 0.01
            ):
                raise ValueError("paper-dispatch buy exceeds broker-orderable cash")
        elif (
            self.broker_orderable_cash is not None
            or self.broker_orderable_buy_quantity is not None
        ):
            raise ValueError(
                "paper-dispatch sells cannot carry buy-orderability evidence"
            )
        if self.snapshot_cash > self.snapshot_equity + 0.000001:
            raise ValueError("paper-dispatch snapshot cash cannot exceed equity")
        if (
            self.snapshot_symbol_orderable_quantity
            > self.snapshot_symbol_quantity + 0.000001
        ):
            raise ValueError(
                "paper-dispatch orderable quantity cannot exceed holding quantity"
            )
        if (
            self.side == "sell"
            and self.quantity
            > self.snapshot_symbol_orderable_quantity + 0.000001
        ):
            raise ValueError(
                "paper-dispatch sells cannot exceed snapshot orderable quantity"
            )
        if self.purpose == "rebalance" and self.side == "buy":
            if self.entry_atr14 is None or self.entry_atr14 <= 0:
                raise ValueError(
                    "paper rebalance buys require positive entry ATR14 evidence"
                )
        if self.order_plan_payload is not None:
            try:
                durable_order = OrderPlan.model_validate(self.order_plan_payload)
            except ValueError:
                raise ValueError(
                    "paper-dispatch durable order payload is invalid"
                ) from None
            explanation = durable_order.explanation
            if (
                durable_order.status != OrderStatus.submitted
                or durable_order.order_plan_id != self.order_plan_id
                or durable_order.policy_id != self.policy_id
                or durable_order.policy_version != self.policy_version
                or durable_order.purpose != self.purpose
                or durable_order.idempotency_key != self.idempotency_key
                or durable_order.risk_check_id != self.risk_check_id
                or durable_order.risk_check_expires_at
                != self.risk_check_expires_at
                or durable_order.intent.symbol.strip().upper() != self.symbol
                or durable_order.intent.side != self.side
                or durable_order.intent.order_type.value != self.order_type
                or not isclose(
                    durable_order.intent.quantity,
                    self.quantity,
                    abs_tol=0.000001,
                )
                or durable_order.intent.limit_price is None
                or not isclose(
                    durable_order.intent.limit_price,
                    self.limit_price,
                    abs_tol=0.000001,
                )
                or durable_order.intent.quote_time != self.quote_as_of
                or explanation is None
                or explanation.strategy_id != self.strategy_id
                or explanation.strategy_version != self.strategy_version
            ):
                raise ValueError(
                    "paper-dispatch durable order payload does not match"
                )

        pre_dispatch_states = {
            "prepared",
            "expired_pre_dispatch",
            "failed_pre_dispatch",
        }
        if self.status in pre_dispatch_states:
            if self.attempt_count != 0 or self.dispatch_claimed_at is not None:
                raise ValueError("pre-dispatch states cannot claim an external attempt")
            if (
                self.broker_business_date is not None
                or self.broker_order_reference is not None
                or self.broker_forwarding_order_org_number is not None
                or self.broker_order_branch_number is not None
                or self.broker_order_time is not None
                or self.cumulative_filled_quantity > 0.000001
                or self.fill_evidence
            ):
                raise ValueError("pre-dispatch states cannot contain broker evidence")
        else:
            if self.attempt_count != 1 or self.dispatch_claimed_at is None:
                raise ValueError("post-dispatch states require exactly one claimed attempt")
            if self.dispatch_claimed_at < self.prepared_at:
                raise ValueError("dispatch claim cannot precede preparation")
            if self.dispatch_claimed_at > self.updated_at:
                raise ValueError("dispatch claim cannot occur after the latest update")
        if self.status in {"dispatch_claimed", "outcome_unknown"} and (
            self.broker_business_date is not None
            or self.broker_order_reference is not None
            or self.broker_forwarding_order_org_number is not None
            or self.broker_order_branch_number is not None
            or self.broker_order_time is not None
            or self.cumulative_filled_quantity > 0.000001
            or self.fill_evidence
        ):
            raise ValueError("uncertain paper dispatches cannot assert broker evidence")

        evidence_quantity = sum(item.quantity for item in self.fill_evidence)
        if not isclose(
            evidence_quantity,
            self.cumulative_filled_quantity,
            rel_tol=0,
            abs_tol=0.000001,
        ):
            raise ValueError("cumulative fills must equal durable fill evidence")
        if self.cumulative_filled_quantity > self.quantity + 0.000001:
            raise ValueError("paper-dispatch fills cannot exceed requested quantity")
        for fill in self.fill_evidence:
            if (
                fill.symbol != self.symbol
                or fill.side != self.side
                or fill.broker_order_id != self.broker_order_id
                or self.broker_order_reference is None
                or fill.broker_order_reference != self.broker_order_reference
                or fill.evidence_at > self.updated_at
            ):
                raise ValueError("paper-dispatch fill evidence does not match its order")

        broker_identified_states = {
            "accepted",
            "partially_filled",
            "filled",
            "cancelled",
        }
        if self.status in broker_identified_states and (
            self.broker_order_reference is None
            or self.broker_business_date is None
            or self.broker_order_time is None
        ):
            raise ValueError(
                "observed paper orders require complete KIS order identity"
            )
        if self.status == "accepted" and (
            self.broker_forwarding_order_org_number is None
            and self.broker_order_branch_number is None
        ):
            raise ValueError(
                "accepted paper orders require forwarding or branch evidence"
            )
        if self.status in {"partially_filled", "filled", "cancelled"} and (
            self.broker_order_branch_number is None
        ):
            raise ValueError(
                "daily-observed paper orders require order-branch evidence"
            )
        if self.status == "accepted" and (
            self.broker_order_reference is None
            or self.cumulative_filled_quantity > 0.000001
        ):
            raise ValueError("accepted paper orders require a reference and zero fills")
        if self.status == "partially_filled" and not (
            self.broker_order_reference is not None
            and 0 < self.cumulative_filled_quantity < self.quantity - 0.000001
        ):
            raise ValueError("partially-filled paper orders require partial fill evidence")
        if self.status == "filled" and (
            self.broker_order_reference is None
            or not isclose(
                self.cumulative_filled_quantity,
                self.quantity,
                rel_tol=0,
                abs_tol=0.000001,
            )
        ):
            raise ValueError("filled paper orders require complete fill evidence")
        if self.status == "rejected" and self.cumulative_filled_quantity > 0.000001:
            raise ValueError("rejected paper orders cannot contain fills")

        terminal_reconcilable = {
            "filled",
            "rejected",
            "cancelled",
            "expired_pre_dispatch",
            "failed_pre_dispatch",
        }
        if self.reconciliation_status == "reconciled":
            if self.status not in terminal_reconcilable or self.reconciled_at is None:
                raise ValueError("only terminal paper dispatches can be reconciled")
            if self.reconciled_at < self.updated_at:
                raise ValueError("reconciliation time cannot precede the latest evidence")
        elif self.reconciled_at is not None:
            raise ValueError("reconciliation time requires reconciled status")
        return self


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
    data_mode: Literal["fixture", "paper_trading"] = "fixture"
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
