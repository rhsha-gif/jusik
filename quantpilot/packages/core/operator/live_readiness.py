from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field

from quantpilot.packages.core.schemas import DataMode, HarnessModel, new_id, utc_now


LiveReadinessGateName = Literal[
    "legal_api_terms_review",
    "max_capital_at_risk",
    "secret_management_review",
    "realtime_staleness_review",
    "exchange_calendar_halt_handling",
    "reconciliation_review",
    "paper_track_record_review",
    "kill_switch_drill",
    "incident_runbook_review",
    "order_path_review",
]
OwnerType = Literal["human", "ai", "codex", "claude", "automation", "system"]


class LiveReadinessGate(HarnessModel):
    gate: LiveReadinessGateName
    label: str
    human_only: bool = True
    required: bool = True
    max_age_days: int = Field(default=90, gt=0)


class LiveReadinessEvidence(HarnessModel):
    evidence_id: str = Field(default_factory=lambda: new_id("lvev"))
    gate: LiveReadinessGateName
    owner_type: OwnerType
    owner_id: str
    summary: str
    reviewed_at: datetime
    expires_at: datetime | None = None
    evidence_uri: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    contains_secret_like_content: bool = False


class LiveReadinessDecision(HarnessModel):
    gate: LiveReadinessGateName
    status: Literal["passed", "blocked"]
    reason: str
    evidence_id: str | None = None
    owner_type: OwnerType | None = None
    human_only: bool = True
    stale: bool = False
    secret_like_evidence_rejected: bool = False
    evaluated_at: datetime = Field(default_factory=utc_now)


class LiveReadinessReport(HarnessModel):
    report_id: str = Field(default_factory=lambda: new_id("lvready"))
    status: Literal["candidate_ready", "blocked"]
    data_mode: Literal["live_trading_candidate"] = DataMode.live_trading_candidate.value
    generated_at: datetime = Field(default_factory=utc_now)
    gates: list[LiveReadinessGate]
    decisions: list[LiveReadinessDecision]
    evidence_records: list[LiveReadinessEvidence] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool | str] = Field(default_factory=dict)
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False


class LiveReadinessEvaluationRequest(HarnessModel):
    evidence: list[LiveReadinessEvidence] = Field(default_factory=list)


LIVE_READINESS_GATES: tuple[LiveReadinessGate, ...] = (
    LiveReadinessGate(gate="legal_api_terms_review", label="Legal and broker API terms reviewed"),
    LiveReadinessGate(gate="max_capital_at_risk", label="Maximum capital at risk approved"),
    LiveReadinessGate(gate="secret_management_review", label="Secret management reviewed"),
    LiveReadinessGate(gate="realtime_staleness_review", label="Realtime data staleness guarantees reviewed"),
    LiveReadinessGate(gate="exchange_calendar_halt_handling", label="Exchange calendar and halt handling reviewed"),
    LiveReadinessGate(gate="reconciliation_review", label="Broker reconciliation reviewed"),
    LiveReadinessGate(gate="paper_track_record_review", label="Paper trading track record reviewed"),
    LiveReadinessGate(gate="kill_switch_drill", label="Kill switch drill completed"),
    LiveReadinessGate(gate="incident_runbook_review", label="Incident runbook reviewed"),
    LiveReadinessGate(gate="order_path_review", label="Order path independently reviewed"),
)


def _live_trading_env_enabled() -> bool:
    return os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _secret_like(evidence: LiveReadinessEvidence) -> bool:
    if evidence.contains_secret_like_content:
        return True
    haystack = " ".join(
        [
            evidence.summary,
            evidence.evidence_uri or "",
            " ".join(f"{key}={value}" for key, value in evidence.metadata.items()),
        ]
    ).lower()
    secret_markers = ("secret", "token", "api_key", "app_key", "appsecret", "authorization", "password")
    return any(marker in haystack for marker in secret_markers)


def _evidence_stale(gate: LiveReadinessGate, evidence: LiveReadinessEvidence, now: datetime) -> bool:
    if evidence.expires_at is not None and evidence.expires_at <= now:
        return True
    return evidence.reviewed_at + timedelta(days=gate.max_age_days) <= now


def evaluate_live_readiness(
    evidence: list[LiveReadinessEvidence],
    *,
    now: datetime | None = None,
) -> LiveReadinessReport:
    evaluated_at = now or utc_now()
    evidence_by_gate: dict[LiveReadinessGateName, LiveReadinessEvidence] = {
        record.gate: record for record in evidence
    }
    decisions: list[LiveReadinessDecision] = []
    blocking_reasons: list[str] = []

    for gate in LIVE_READINESS_GATES:
        record = evidence_by_gate.get(gate.gate)
        if record is None:
            reason = "missing_human_evidence"
            blocking_reasons.append(f"{gate.gate}:{reason}")
            decisions.append(LiveReadinessDecision(gate=gate.gate, status="blocked", reason=reason))
            continue
        if gate.human_only and record.owner_type != "human":
            reason = "human_review_required"
            blocking_reasons.append(f"{gate.gate}:{reason}")
            decisions.append(
                LiveReadinessDecision(
                    gate=gate.gate,
                    status="blocked",
                    reason=reason,
                    evidence_id=record.evidence_id,
                    owner_type=record.owner_type,
                )
            )
            continue
        stale = _evidence_stale(gate, record, evaluated_at)
        secret_rejected = _secret_like(record)
        if stale or secret_rejected:
            reason = "stale_evidence" if stale else "secret_like_evidence_rejected"
            blocking_reasons.append(f"{gate.gate}:{reason}")
            decisions.append(
                LiveReadinessDecision(
                    gate=gate.gate,
                    status="blocked",
                    reason=reason,
                    evidence_id=record.evidence_id,
                    owner_type=record.owner_type,
                    stale=stale,
                    secret_like_evidence_rejected=secret_rejected,
                )
            )
            continue
        decisions.append(
            LiveReadinessDecision(
                gate=gate.gate,
                status="passed",
                reason="human_evidence_current",
                evidence_id=record.evidence_id,
                owner_type=record.owner_type,
            )
        )

    live_env_enabled = _live_trading_env_enabled()
    if live_env_enabled:
        blocking_reasons.append("LIVE_TRADING_ENABLED_true_blocks_readiness")

    return LiveReadinessReport(
        status="blocked" if blocking_reasons else "candidate_ready",
        gates=list(LIVE_READINESS_GATES),
        decisions=decisions,
        evidence_records=list(evidence),
        blocking_reasons=blocking_reasons,
        safety_flags={
            "LIVE_TRADING_ENABLED": live_env_enabled,
            "BROKER_MODE": "mock",
            "MARKET_ORDERS_ENABLED": False,
            "order_submission_enabled": False,
        },
        live_trading_enabled=False,
        order_submission_enabled=False,
    )
