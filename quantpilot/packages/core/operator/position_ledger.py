"""Deterministic paper-position and operator-run persistence models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from quantpilot.packages.core.schemas import HarnessModel


PaperRunStatus = Literal["started", "completed", "blocked", "failed"]


def _require_aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return value


class ManagedPositionState(HarnessModel):
    """Allowlisted state required to resume management of one paper position."""

    strategy_id: str
    strategy_version: str
    symbol: str
    quantity: float = Field(gt=0)
    average_entry_price: float = Field(gt=0)
    atr14: float = Field(ge=0)
    active_stop: float = Field(gt=0)
    policy_version: int = Field(ge=1)
    opened_at: datetime
    updated_at: datetime

    @field_validator("strategy_id", "strategy_version", "symbol")
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

    @field_validator("opened_at", "updated_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)

    @model_validator(mode="after")
    def update_cannot_precede_open(self) -> "ManagedPositionState":
        if self.updated_at < self.opened_at:
            raise ValueError("updated_at cannot precede opened_at")
        return self

    @property
    def storage_key(self) -> tuple[str, str, str]:
        return self.strategy_id, self.strategy_version, self.symbol


class PaperRunCheckpoint(HarnessModel):
    """Minimal, secret-free checkpoint for idempotent paper operator cycles."""

    run_id: str
    idempotency_key: str
    policy_version: int = Field(ge=1)
    status: PaperRunStatus
    data_mode: Literal["paper_trading"] = "paper_trading"
    started_at: datetime
    updated_at: datetime

    @field_validator("run_id", "idempotency_key")
    @classmethod
    def identity_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("run identity fields must not be blank")
        return normalized

    @field_validator("started_at", "updated_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)

    @model_validator(mode="after")
    def update_cannot_precede_start(self) -> "PaperRunCheckpoint":
        if self.updated_at < self.started_at:
            raise ValueError("updated_at cannot precede started_at")
        return self
