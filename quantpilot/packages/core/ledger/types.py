from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field

from quantpilot.packages.core.schemas import HarnessModel, new_id, utc_now


LedgerSource = Literal["mock", "paper"]
LedgerDataMode = Literal["fixture", "paper_trading"]


class LedgerEventType(str, Enum):
    order_intent = "order_intent"
    submitted = "submitted"
    fill = "fill"
    partial_fill = "partial_fill"
    cancel = "cancel"
    reject = "reject"
    position_update = "position_update"


class LedgerEntry(HarnessModel):
    ledger_entry_id: str = Field(default_factory=lambda: new_id("ledger"))
    event_type: LedgerEventType
    policy_id: str
    policy_version: int
    order_plan_id: str | None = None
    intent_id: str | None = None
    broker_order_id: str | None = None
    fill_id: str | None = None
    idempotency_key: str
    dedupe_key: str
    source: LedgerSource
    data_mode: LedgerDataMode = "fixture"
    symbol: str | None = None
    side: Literal["buy", "sell"] | None = None
    quantity: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    notional: float | None = Field(default=None, gt=0)
    order_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    sequence: int = Field(default=0, ge=0)
    occurred_at: datetime = Field(default_factory=utc_now)


class ReconciliationLedger(HarnessModel):
    ledger_id: str = Field(default_factory=lambda: new_id("recon"))
    entries: list[LedgerEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
