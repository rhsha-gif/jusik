from __future__ import annotations

from typing import Literal

from quantpilot.packages.core.schemas import HarnessModel


class FallbackDecision(HarnessModel):
    from_level: Literal[5]
    to_level: Literal[4, 3, 2, 0]
    reason_code: str
    detail: str
    order_submission_enabled: bool = False


# Deterministic Level 5 fallback matrix. to_level=0 means no-op; 4 means guarded
# autopilot remains available; 3 means approval-based proposals only; 2 means
# suggestions/reports only. order_submission_enabled is always False: a fallback
# never re-enables automatic submission on its own.
FALLBACK_MATRIX: dict[str, tuple[Literal[4, 3, 2, 0], str]] = {
    "level5_flag_disabled": (0, "FULLY_AUTOMATED_OPERATOR_ENABLED is false; operator run is a no-op"),
    "kill_switch_engaged": (0, "kill switch stops all automatic trading"),
    "operator_kill_switch_engaged": (0, "OPERATOR_KILL_SWITCH blocks all automatic trading"),
    "live_trading_flag_engaged": (0, "LIVE_TRADING_ENABLED must remain false; operator refuses to run"),
    "policy_review_required": (0, "policy version drift requires explicit user review before any automation"),
    "policy_not_found": (0, "no active policy exists for the requested policy id"),
    "monthly_loss_stop_engaged": (0, "monthly loss stop halts all automatic trading"),
    "broker_mode_unsafe": (0, "broker mode is not mock or paper; automation refuses to run"),
    "run_mode_broker_mismatch": (0, "requested run mode does not match the policy broker mode"),
    "policy_not_promoted": (4, "policy is not promoted to Level 5; guarded autopilot is the highest allowed level"),
    "no_level5_strategy_eligible": (4, "no validated_l5 strategy is eligible; fall back to guarded autopilot rails"),
    "no_approved_strategy_available": (2, "no approved strategy at any autopilot level; suggestions and reports only"),
    "monthly_loss_pause_engaged": (3, "monthly loss pause blocks new automatic buys; proposals only"),
    "stale_market_data": (3, "stale quotes block automatic submission; Level 3 proposals only"),
    "broker_unhealthy": (3, "broker heartbeat failed; no automatic submission"),
    "portfolio_snapshot_missing": (0, "broker did not provide a portfolio snapshot; operator run is a no-op"),
    "portfolio_snapshot_fixture": (0, "fixture portfolio snapshot is not allowed on the runtime submission path"),
    "stale_portfolio_snapshot": (3, "stale portfolio snapshot blocks automatic submission; Level 3 proposals only"),
    "broker_failure": (3, "broker error during submission; execution paused and reported"),
    "market_orders_disabled": (3, "market orders are disabled; blocked order requires manual review"),
    "risk_check_failed": (2, "deterministic risk gate failed; suggestions only"),
    "llm_unavailable": (2, "LLM unavailable; deterministic template reports only"),
}


class FallbackManager:
    def for_reason(self, reason_code: str) -> FallbackDecision:
        entry = FALLBACK_MATRIX.get(reason_code)
        if entry is None:
            # Unknown blockers degrade to the safest outcome: full no-op.
            return FallbackDecision(
                from_level=5,
                to_level=0,
                reason_code=reason_code,
                detail=f"unknown Level 5 blocker '{reason_code}'; defaulting to no-op",
                order_submission_enabled=False,
            )
        to_level, detail = entry
        return FallbackDecision(
            from_level=5,
            to_level=to_level,
            reason_code=reason_code,
            detail=detail,
            order_submission_enabled=False,
        )
