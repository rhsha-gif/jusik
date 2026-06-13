from __future__ import annotations

import pytest

from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.strategies.promotion import (
    PROMOTION_CONFIRMATION,
    ImmutableStrategyVersion,
    InvalidPromotionTransition,
    MissingPromotionEvidence,
    PromotionConfirmationRequired,
    PromotionEvidence,
    StrategyLifecycleStatus,
    StrategyPromotionService,
    StrategyVersionMismatch,
    compute_spec_hash,
    eligibility_for,
    load_lifecycle_fixture,
)


def _backtest_evidence() -> PromotionEvidence:
    return PromotionEvidence(
        kind="backtest_result",
        reference="docs/stage_03_backtest_validation_report.md",
        summary="backtest cleared the validation protocol",
        metrics={"excess_return": 0.04, "max_drawdown": -0.08},
        recorded_by="human-reviewer",
    )


def _draft_service(strategy_id: str = "alpha_v1", version: str = "1.0") -> StrategyPromotionService:
    service = StrategyPromotionService()
    service.register_draft(strategy_id=strategy_id, version=version, spec_hash="sha256:spec-1")
    return service


# -- draft cannot submit --------------------------------------------------
def test_draft_can_backtest_but_cannot_submit() -> None:
    eligibility = eligibility_for(StrategyLifecycleStatus.draft)
    assert eligibility.can_backtest is True
    assert eligibility.can_submit_orders is False
    assert eligibility.justified_registry_levels == ()


def test_backtested_still_cannot_submit() -> None:
    eligibility = eligibility_for(StrategyLifecycleStatus.backtested)
    assert eligibility.can_backtest is True
    assert eligibility.can_submit_orders is False
    assert eligibility.justified_registry_levels == ()


# -- backtest evidence -> paper_candidate only with human confirmation ----
def test_promotion_requires_human_confirmation_marker() -> None:
    service = _draft_service()
    service.attach_evidence(strategy_id="alpha_v1", version="1.0", evidence=_backtest_evidence())
    # draft -> backtested first (also needs backtest evidence + confirmation)
    service.promote(
        strategy_id="alpha_v1",
        version="1.0",
        confirmation=PROMOTION_CONFIRMATION,
        confirmed_by="human-reviewer",
    )

    # backtested -> paper_candidate with the WRONG confirmation is rejected.
    with pytest.raises(PromotionConfirmationRequired):
        service.promote(
            strategy_id="alpha_v1",
            version="1.0",
            confirmation="looks good to me",
            confirmed_by="human-reviewer",
        )
    # An empty confirmed_by is also rejected even with the right phrase.
    with pytest.raises(PromotionConfirmationRequired):
        service.promote(
            strategy_id="alpha_v1",
            version="1.0",
            confirmation=PROMOTION_CONFIRMATION,
            confirmed_by="   ",
        )
    assert service.require("alpha_v1").status is StrategyLifecycleStatus.backtested

    # With the exact human marker the strategy advances and becomes submit-eligible.
    record = service.promote(
        strategy_id="alpha_v1",
        version="1.0",
        confirmation=PROMOTION_CONFIRMATION,
        confirmed_by="human-reviewer",
    )
    assert record.status is StrategyLifecycleStatus.paper_candidate
    assert service.eligibility("alpha_v1").can_submit_orders is True
    assert record.history[-1].confirmed_by == "human-reviewer"


def test_promotion_without_required_evidence_is_blocked() -> None:
    service = _draft_service()
    with pytest.raises(MissingPromotionEvidence):
        service.promote(
            strategy_id="alpha_v1",
            version="1.0",
            confirmation=PROMOTION_CONFIRMATION,
            confirmed_by="human-reviewer",
        )
    assert service.require("alpha_v1").status is StrategyLifecycleStatus.draft


def test_promotion_skipping_a_ladder_step_is_invalid() -> None:
    service = _draft_service()
    service.attach_evidence(strategy_id="alpha_v1", version="1.0", evidence=_backtest_evidence())
    with pytest.raises(InvalidPromotionTransition):
        service.promote(
            strategy_id="alpha_v1",
            version="1.0",
            target=StrategyLifecycleStatus.paper_candidate,
            confirmation=PROMOTION_CONFIRMATION,
            confirmed_by="human-reviewer",
        )


# -- revoked strategy is never eligible -----------------------------------
def test_revoked_strategy_is_never_eligible() -> None:
    eligibility = eligibility_for(StrategyLifecycleStatus.revoked)
    assert eligibility.can_backtest is False
    assert eligibility.can_submit_orders is False
    assert eligibility.justified_registry_levels == ()


def test_revoked_strategy_cannot_be_promoted_out() -> None:
    service = _draft_service()
    service.revoke(strategy_id="alpha_v1", reason="user_withdrew_strategy")
    assert service.require("alpha_v1").status is StrategyLifecycleStatus.revoked
    with pytest.raises(InvalidPromotionTransition):
        service.promote(
            strategy_id="alpha_v1",
            version="1.0",
            confirmation=PROMOTION_CONFIRMATION,
            confirmed_by="human-reviewer",
        )
    # And it is terminal: it cannot even be re-revoked or disabled.
    with pytest.raises(InvalidPromotionTransition):
        service.disable(strategy_id="alpha_v1", reason="noop")


# -- version mismatch blocks promotion ------------------------------------
def test_version_mismatch_blocks_promotion() -> None:
    service = _draft_service(version="1.0")
    service.attach_evidence(strategy_id="alpha_v1", version="1.0", evidence=_backtest_evidence())
    with pytest.raises(StrategyVersionMismatch):
        service.promote(
            strategy_id="alpha_v1",
            version="2.0",
            confirmation=PROMOTION_CONFIRMATION,
            confirmed_by="human-reviewer",
        )


# -- immutability of a promoted version -----------------------------------
def test_mismatched_spec_hash_is_rejected() -> None:
    service = _draft_service()
    service.attach_evidence(strategy_id="alpha_v1", version="1.0", evidence=_backtest_evidence())
    with pytest.raises(ImmutableStrategyVersion):
        service.promote(
            strategy_id="alpha_v1",
            version="1.0",
            spec_hash="sha256:tampered-spec",
            confirmation=PROMOTION_CONFIRMATION,
            confirmed_by="human-reviewer",
        )


def test_spec_hash_is_deterministic_and_order_independent() -> None:
    recipe = load_strategy_recipe("pullback_trend_v2")
    again = load_strategy_recipe("pullback_trend_v2")
    assert compute_spec_hash(recipe) == compute_spec_hash(again)
    assert compute_spec_hash(recipe).startswith("sha256:")


# -- fixture: durable versioned representation ----------------------------
def test_fixture_records_load_with_expected_lifecycle_states() -> None:
    records = {record.strategy_id: record for record in load_lifecycle_fixture()}

    draft = records["pullback_trend_v2"]
    assert draft.status is StrategyLifecycleStatus.draft
    assert eligibility_for(draft.status).can_submit_orders is False

    validated = records["pullback_trend_v1"]
    assert validated.status is StrategyLifecycleStatus.paper_validated
    assert eligibility_for(validated.status).can_submit_orders is True
    assert "backtest_result" in validated.evidence_kinds()


def test_full_ladder_promotes_with_evidence_and_confirmation() -> None:
    service = _draft_service()
    service.attach_evidence(strategy_id="alpha_v1", version="1.0", evidence=_backtest_evidence())
    service.attach_evidence(
        strategy_id="alpha_v1",
        version="1.0",
        evidence=PromotionEvidence(
            kind="paper_track_record",
            reference="paper-run-001",
            summary="20 paper sessions, no violations",
            recorded_by="human-reviewer",
        ),
    )
    service.attach_evidence(
        strategy_id="alpha_v1",
        version="1.0",
        evidence=PromotionEvidence(
            kind="risk_review",
            reference="risk-signoff-001",
            summary="risk matrix re-reviewed",
            recorded_by="risk-gatekeeper",
        ),
    )

    for expected in (
        StrategyLifecycleStatus.backtested,
        StrategyLifecycleStatus.paper_candidate,
        StrategyLifecycleStatus.paper_validated,
        StrategyLifecycleStatus.live_candidate,
    ):
        record = service.promote(
            strategy_id="alpha_v1",
            version="1.0",
            confirmation=PROMOTION_CONFIRMATION,
            confirmed_by="human-reviewer",
        )
        assert record.status is expected

    assert len(service.require("alpha_v1").history) == 4
    # live_candidate justifies level-5 candidacy but is still only a candidate.
    assert "fully_automated" in eligibility_for(StrategyLifecycleStatus.live_candidate).justified_registry_levels
