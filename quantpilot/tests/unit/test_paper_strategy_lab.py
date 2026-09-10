from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from quantpilot.paper.lab import (
    Candidate,
    FIRST_PAPER_TRIAL_CAP,
    ForwardTradeEvidence,
    IndependentReview,
    LabError,
    LabErrorCode,
    LabState,
    PromotionDecision,
    SandboxConfig,
    SandboxReadiness,
    SignalRunResult,
    VerificationEvidence,
    build_docker_command,
    evaluate_promotion,
    move_to_paper_trial,
    move_to_shadow,
    register_generated,
    replace_source,
    run_candidate_generation,
    run_candidate_review,
    run_signal_code,
    verify_sandbox_readiness,
)

NOW = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)
IMAGE = "python@sha256:" + "a" * 64
SOURCE = "def generate_signals(evidence):\n    return []\n"


def candidate(
    index: int = 1, generated_at: datetime = NOW - timedelta(days=10)
) -> Candidate:
    return Candidate.create(
        candidate_id=f"candidate-{index}",
        strategy_id="pullback",
        version=f"v{index}",
        source=SOURCE,
        generator_provider="claude",
        generated_at=generated_at,
    )


def promotion_evidence(
    item: Candidate,
) -> tuple[list[ForwardTradeEvidence], list[VerificationEvidence], IndependentReview]:
    sessions = [
        NOW.date(),
        (NOW - timedelta(days=1)).date(),
        (NOW - timedelta(days=2)).date(),
        (NOW - timedelta(days=3)).date(),
        (NOW - timedelta(days=6)).date(),
    ]
    trades = [
        ForwardTradeEvidence(
            trade_id=f"trade-{index}",
            session_date=sessions[index % 5],
            closed_at=datetime.combine(
                sessions[index % 5], datetime.min.time(), tzinfo=timezone.utc
            )
            + timedelta(hours=3),
            state="CLOSED",
            cost_adjusted_net=1.0,
            source_hash=item.source_hash,
            candidate_version=item.version,
            provenance="trusted_external",
        )
        for index in range(20)
    ]
    tests = [
        VerificationEvidence(
            kind=kind,
            passed=True,
            source_hash=item.source_hash,
            candidate_version=item.version,
            observed_at=NOW,
            verifier="ci-runner",
            provenance="trusted_test_runner",
        )
        for kind in ("safety", "reproducibility", "no_lookahead")
    ]
    review = IndependentReview(
        reviewer_provider="codex",
        generator_provider="claude",
        approved=True,
        source_hash=item.source_hash,
        candidate_version=item.version,
        observed_at=NOW,
        summary="Independent safety review passed",
    )
    return trades, tests, review


def test_candidate_hash_is_immutable_and_source_change_resets_identity() -> None:
    original = candidate()
    with pytest.raises(ValidationError):
        Candidate(**{**original.model_dump(), "source_hash": "0" * 64})

    changed = replace_source(
        original,
        source="def generate_signals(evidence):\n    return [{'signal': 'flat'}]\n",
        version="v2",
        generator_provider="codex",
        generated_at=NOW + timedelta(minutes=1),
    )

    assert isinstance(changed, Candidate)
    assert changed.stage == "generated"
    assert changed.allocation_cap == 0.0
    assert changed.source_hash != original.source_hash


def test_promotion_requires_twenty_closed_trades_across_five_sessions_and_positive_net() -> (
    None
):
    item = candidate()
    trades, tests, review = promotion_evidence(item)

    decision = evaluate_promotion(item, trades, tests, review, NOW)

    assert decision.eligible
    assert decision.first_paper_trial_cap == FIRST_PAPER_TRIAL_CAP

    insufficient = evaluate_promotion(item, trades[:19], tests, review, NOW)
    assert not insufficient.eligible
    assert "insufficient_unique_closed_trades" in insufficient.reasons

    losing = [trade.model_copy(update={"cost_adjusted_net": -1.0}) for trade in trades]
    assert (
        "nonpositive_cost_adjusted_net"
        in evaluate_promotion(item, losing, tests, review, NOW).reasons
    )


def test_promotion_rejects_future_cross_version_hash_and_self_attestation() -> None:
    item = candidate()
    trades, tests, review = promotion_evidence(item)
    trades[0] = trades[0].model_copy(update={"closed_at": NOW + timedelta(seconds=1)})
    tests[0] = tests[0].model_copy(update={"source_hash": "f" * 64, "verifier": "self"})
    review = review.model_copy(update={"candidate_version": "old"})

    decision = evaluate_promotion(item, trades, tests, review, NOW)

    assert not decision.eligible
    assert {
        "future_trade_evidence",
        "test_identity_mismatch",
        "self_attestation",
        "review_identity_mismatch",
    }.issubset(decision.reasons)


def test_hash_reset_makes_previous_evidence_ineligible() -> None:
    original = candidate()
    trades, tests, review = promotion_evidence(original)
    changed = replace_source(
        original,
        source=SOURCE + "# changed\n",
        version="v2",
        generator_provider="claude",
        generated_at=NOW,
    )
    assert isinstance(changed, Candidate)

    decision = evaluate_promotion(changed, trades, tests, review, NOW)

    assert not decision.eligible
    assert "trade_identity_mismatch" in decision.reasons
    assert "test_identity_mismatch" in decision.reasons
    assert "review_identity_mismatch" in decision.reasons


def test_generation_shadow_and_paper_trial_budgets() -> None:
    state = LabState()
    first = candidate(1, NOW - timedelta(days=3))
    state = register_generated(state, first, NOW)
    assert isinstance(state, LabState)

    same_day = candidate(2, NOW - timedelta(days=3, hours=-1))
    assert isinstance(register_generated(state, same_day, NOW), LabError)

    for index, days in ((2, 2), (3, 1), (4, 0)):
        item = candidate(index, NOW - timedelta(days=days))
        state = register_generated(state, item, NOW)
        assert isinstance(state, LabState)
    for index in (1, 2, 3):
        state = move_to_shadow(state, f"candidate-{index}")
        assert isinstance(state, LabState)
    blocked = move_to_shadow(state, "candidate-4")
    assert isinstance(blocked, LabError)
    assert blocked.code is LabErrorCode.SHADOW_LIMIT

    trial_candidate = next(
        item for item in state.candidates if item.candidate_id == "candidate-1"
    )
    approved = PromotionDecision(
        eligible=True,
        reasons=(),
        source_hash=trial_candidate.source_hash,
        candidate_version=trial_candidate.version,
        first_paper_trial_cap=0.10,
    )
    state = move_to_paper_trial(state, "candidate-1", approved)
    assert isinstance(state, LabState)
    assert (
        next(
            item for item in state.candidates if item.candidate_id == "candidate-1"
        ).allocation_cap
        == 0.10
    )
    second = next(
        item for item in state.candidates if item.candidate_id == "candidate-2"
    )
    wrong_identity = move_to_paper_trial(state, "candidate-2", approved)
    assert isinstance(wrong_identity, LabError)
    assert wrong_identity.code is LabErrorCode.PROMOTION_BLOCKED
    second_approval = approved.model_copy(
        update={"source_hash": second.source_hash, "candidate_version": second.version}
    )
    blocked_trial = move_to_paper_trial(state, "candidate-2", second_approval)
    assert isinstance(blocked_trial, LabError)
    assert blocked_trial.code is LabErrorCode.PAPER_TRIAL_LIMIT


def test_generation_and_review_adapters_keep_source_as_data_and_require_independence() -> (
    None
):
    calls: list[str] = []

    def runner(
        provider: str, _prompt: str, schema: dict[str, object]
    ) -> dict[str, object]:
        calls.append(provider)
        if "source" in schema["properties"]:
            return {
                "model": "generator",
                "strategy_id": "pullback",
                "version": "v1",
                "source": SOURCE,
            }
        return {
            "model": "reviewer",
            "approved": True,
            "summary": "Safe and reproducible",
        }

    generated = run_candidate_generation({}, NOW, "claude", runner)
    assert not isinstance(generated, LabError)
    assert calls == ["claude"]
    item = candidate()
    assert (
        run_candidate_review(item, {}, NOW, "claude", runner).code
        is LabErrorCode.INDEPENDENCE_REQUIRED
    )
    reviewed = run_candidate_review(item, {}, NOW, "codex", runner)
    assert isinstance(reviewed, IndependentReview)
    assert reviewed.source_hash == item.source_hash


def test_sandbox_disabled_and_unpinned_configuration_fail_closed() -> None:
    with pytest.raises(ValidationError):
        SandboxConfig(enabled=True, pinned_image="python:latest")

    config = SandboxConfig(enabled=False, pinned_image=IMAGE)
    called = False

    def runner(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("must not execute")

    assert (
        verify_sandbox_readiness(config, NOW, runner).code
        is LabErrorCode.SANDBOX_DISABLED
    )
    assert (
        run_signal_code(candidate(), {}, config, None, runner).code
        is LabErrorCode.SANDBOX_DISABLED
    )
    assert not called


def test_readiness_failure_blocks_execution() -> None:
    config = SandboxConfig(enabled=True, pinned_image=IMAGE)

    def failed(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="secret raw stderr")

    result = verify_sandbox_readiness(config, NOW, failed)

    assert isinstance(result, LabError)
    assert result.code is LabErrorCode.SANDBOX_NOT_READY
    assert "secret" not in result.model_dump_json()


def test_docker_command_and_fake_subprocess_enforce_guards() -> None:
    config = SandboxConfig(enabled=True, pinned_image=IMAGE)
    readiness = SandboxReadiness(
        pinned_image=IMAGE, image_id="sha256:" + "b" * 64, verified_at=NOW
    )
    captured: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> SimpleNamespace:
        if command[:2] == ["docker", "rm"]:
            captured["cleanup"] = command
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=b'{"suggestions":[{"symbol":"005930","strategy_id":"pullback","signal":"long","confidence":0.7,"reason":"setup"}]}',
            stderr=b"",
        )

    result = run_signal_code(candidate(), {"bars": []}, config, readiness, runner)

    assert isinstance(result, SignalRunResult)
    assert (
        captured["cleanup"][-1]
        == captured["command"][captured["command"].index("--name") + 1]
    )
    command = captured["command"]
    assert isinstance(command, list)
    for guard in (
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    ):
        assert guard in command
    assert command.count("--mount") == 2
    assert not any(
        "docker.sock" in argument or ".env" in argument or "repo" in argument
        for argument in command
    )
    assert captured["kwargs"]["shell"] is False
    assert result.source_hash == candidate().source_hash


def test_command_builder_rejects_relative_mounts_and_output_cannot_be_an_order() -> (
    None
):
    config = SandboxConfig(enabled=True, pinned_image=IMAGE)
    with pytest.raises(ValueError):
        build_docker_command(config, Path("candidate.py"), Path("input.json"))

    readiness = SandboxReadiness(
        pinned_image=IMAGE, image_id="sha256:" + "b" * 64, verified_at=NOW
    )

    def runner(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=b'{"suggestions":[{"symbol":"005930","strategy_id":"pullback","signal":"long","confidence":0.7,"reason":"setup","quantity":10}]}',
            stderr=b"",
        )

    result = run_signal_code(candidate(), {}, config, readiness, runner)
    assert isinstance(result, LabError)
    assert result.code is LabErrorCode.MALFORMED_OUTPUT


def test_signal_runner_bounds_time_and_output() -> None:
    config = SandboxConfig(enabled=True, pinned_image=IMAGE, max_output_bytes=1024)
    readiness = SandboxReadiness(
        pinned_image=IMAGE, image_id="sha256:" + "b" * 64, verified_at=NOW
    )

    def timeout(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired("docker", 10)

    assert (
        run_signal_code(candidate(), {}, config, readiness, timeout).code
        is LabErrorCode.SANDBOX_TIMEOUT
    )

    def oversized(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=b"x" * 1025, stderr=b"")

    assert (
        run_signal_code(candidate(), {}, config, readiness, oversized).code
        is LabErrorCode.SANDBOX_OUTPUT_LIMIT
    )


def test_forward_evidence_rejects_nan() -> None:
    item = candidate()
    with pytest.raises(ValidationError):
        ForwardTradeEvidence(
            trade_id="trade",
            session_date=NOW.date(),
            closed_at=NOW,
            state="CLOSED",
            cost_adjusted_net=float("nan"),
            source_hash=item.source_hash,
            candidate_version=item.version,
            provenance="trusted_external",
        )
