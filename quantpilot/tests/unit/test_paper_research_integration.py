from datetime import datetime, timedelta, timezone
import pytest

from quantpilot.paper.store import Store
from quantpilot.paper.research import static_gate, postclose

NOW = datetime(2026, 9, 10, 6, tzinfo=timezone.utc)


def test_disabled_research_does_not_invoke_provider_or_docker(tmp_path):
    store = Store(tmp_path / "state")

    def forbidden(*args, **kwargs):
        pytest.fail("disabled research invoked external capability")

    assert postclose(
        store, NOW, {"risks": ["reason"]}, runner=forbidden, readiness_check=forbidden
    ) == {"status": "disabled"}
    store.close()


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "def generate_signals(e): return open('x')",
        "def generate_signals(e): return e.__class__",
    ],
)
def test_pure_code_gate_rejects_host_access(source):
    with pytest.raises(ValueError):
        static_gate(source)


def test_failed_sandbox_does_not_generate_or_change_capital(tmp_path):
    from quantpilot.paper.lab import LabError, LabErrorCode

    store = Store(tmp_path / "state")
    store.configure(
        {"research_enabled": True, "sandbox_image": "python@sha256:" + "a" * 64}, 1
    )
    result = postclose(
        store,
        NOW,
        {"risks": ["reason"]},
        runner=lambda *args: pytest.fail("provider must not run"),
        readiness_check=lambda *args: LabError(code=LabErrorCode.SANDBOX_NOT_READY),
    )
    assert result["reason"] == "sandbox_not_ready"
    assert store.get("cash") == 5_000_000
    assert store.db.execute("SELECT COUNT(*) FROM lab").fetchone()[0] == 0
    store.close()


def test_assessment_roundtrip_and_expiry(tmp_path):
    from quantpilot.paper.intelligence import Assessment
    from quantpilot.paper.runtime import Runtime

    store = Store(tmp_path / "state")
    store.configure({"ai_enabled": True}, 1)
    value = Assessment(
        provider="claude",
        model="fixture",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=75),
        candidate_scores={"005930": 0.5},
    )
    store.put("assessment", value.model_dump(mode="json"))
    runtime = Runtime(store, None, None, None)
    assert runtime.assessment(NOW) == value
    assert runtime.assessment(NOW + timedelta(minutes=75)) is None
    store.close()


def test_trusted_forward_evidence_promotes_only_one_capped_trial(tmp_path):
    from quantpilot.tests.unit.test_paper_strategy_lab import (
        candidate,
        promotion_evidence,
        IMAGE,
    )
    from quantpilot.paper.lab import SandboxReadiness
    from quantpilot.paper.research import save, candidates

    store = Store(tmp_path / "state")
    store.configure({"research_enabled": True, "sandbox_image": IMAGE}, 1)
    item = candidate().model_copy(update={"stage": "shadow"})
    save(store, item)
    trades, tests, review = promotion_evidence(item)
    store.put(
        "lab_trades:" + item.source_hash, [t.model_dump(mode="json") for t in trades]
    )
    store.put(
        "lab_evidence:" + item.source_hash,
        {
            "tests": [t.model_dump(mode="json") for t in tests],
            "review": review.model_dump(mode="json"),
        },
    )
    ready = SandboxReadiness(
        pinned_image=IMAGE, image_id="sha256:" + "b" * 64, verified_at=NOW
    )
    postclose(store, NOW, {}, readiness_check=lambda *args: ready)
    promoted = candidates(store)[0]
    assert promoted.stage == "paper_trial"
    assert promoted.allocation_cap == 0.1
    assert store.get("cash") == 5_000_000
    store.close()


def test_generation_retry_keeps_the_reviewer_independent(tmp_path):
    from quantpilot.paper.lab import SandboxReadiness, SignalRunResult
    from quantpilot.paper.strategy import Bar

    store = Store(tmp_path / "s")
    image = "python@sha256:" + "a" * 64
    store.configure({"research_enabled": True, "sandbox_image": image}, 1)
    store.put("universe", ["005930"])
    store.put("evidence", {"observed_at": NOW.isoformat()})
    store.save_bars(
        [Bar("005930", NOW - timedelta(minutes=1), 100.0, 101.0, 99.0, 100.0, 10.0)]
    )
    calls = []

    def runner(provider, prompt, schema):
        calls.append(provider)
        if len(calls) == 1:
            raise TimeoutError()
        if "source" in schema["properties"]:
            return {
                "model": "fixture",
                "strategy_id": "trial_fixture",
                "version": "1",
                "source": "def generate_signals(evidence):\n    return []\n",
            }
        return {"model": "fixture", "approved": True, "summary": "Reviewed"}

    ready = SandboxReadiness(
        pinned_image=image, image_id="sha256:" + "b" * 64, verified_at=NOW
    )

    def execute(candidate, *args):
        return SignalRunResult(
            source_hash=candidate.source_hash,
            candidate_version=candidate.version,
            suggestions=(),
        )

    result = postclose(
        store,
        NOW,
        {"risks": ["Observed fixture limitation"]},
        runner=runner,
        execute=execute,
        readiness_check=lambda *args: ready,
    )
    assert result["status"] == "shadow"
    assert calls == ["claude", "codex", "claude"]
    store.close()
