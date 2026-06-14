from __future__ import annotations

from datetime import timedelta

import pytest

from quantpilot.packages.brokers.paper_adapter import PaperBrokerAdapter, PaperBrokerConfigError
from quantpilot.packages.core.data.kis_readiness import evaluate_kis_readiness
from quantpilot.packages.core.operator.live_readiness import (
    LIVE_READINESS_GATES,
    LiveReadinessEvidence,
    evaluate_live_readiness,
)
from quantpilot.packages.core.policy.backtest_acceptance import (
    BacktestAcceptanceEvidence,
    evaluate_backtest_acceptance,
)
from quantpilot.packages.core.schemas import utc_now


def test_kis_readiness_is_offline_and_fail_closed_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCESS_TOKEN", "KIS_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    report = evaluate_kis_readiness()

    assert report.status == "blocked"
    assert report.blocking_reasons == ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCESS_TOKEN", "KIS_BASE_URL"]
    assert report.live_trading_enabled is False
    assert report.order_submission_enabled is False


def test_live_readiness_requires_current_human_evidence_and_never_enables_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    now = utc_now()
    evidence = [
        LiveReadinessEvidence(
            gate=gate.gate,
            owner_type="human",
            owner_id="reviewer",
            summary="review complete",
            reviewed_at=now - timedelta(days=1),
        )
        for gate in LIVE_READINESS_GATES
    ]

    report = evaluate_live_readiness(evidence, now=now)

    assert report.status == "blocked"
    assert "LIVE_TRADING_ENABLED_true_blocks_readiness" in report.blocking_reasons
    assert report.live_trading_enabled is False
    assert report.order_submission_enabled is False


def test_paper_adapter_rejects_non_fake_clients() -> None:
    class UnsafeClient:
        is_fake_client = False

    with pytest.raises(PaperBrokerConfigError):
        PaperBrokerAdapter(client=UnsafeClient())


def test_backtest_acceptance_is_research_only() -> None:
    decision = evaluate_backtest_acceptance(
        BacktestAcceptanceEvidence(
            strategy_id="fixture_strategy",
            strategy_version="v1",
            metrics={"filled_trades": 10, "max_drawdown": 0.05, "turnover": 0.4},
        )
    )

    assert decision.status == "accepted_for_review"
    assert decision.research_only is True
    assert decision.live_trading_approval is False
