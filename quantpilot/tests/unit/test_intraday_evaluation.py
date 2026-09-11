from datetime import datetime, timezone
import pytest

from quantpilot.paper.intraday.evaluation import (
    Experiment,
    day_block_test,
    evaluate_candidate,
)
from quantpilot.paper.intraday.deployment import admitted, digest
from quantpilot.paper.store import Store

NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def test_missing_history_never_claims_correction_and_looks_spend_alpha(tmp_path):
    e = Experiment(tmp_path / "research.sqlite3")
    first = evaluate_candidate(e, "trend_pullback", "none", NOW)
    second = evaluate_candidate(e, "trend_pullback", "none", NOW)
    assert first["status"] == "data_insufficient"
    assert not first["multiple_comparison_corrected"]
    assert second["alpha_per_test_this_look"] < first["alpha_per_test_this_look"]
    e.close()


def test_evidence_is_immutable_and_corruption_rejected(tmp_path):
    e = Experiment(tmp_path / "research.sqlite3")
    e.append("family", "test", {"value": 1}, NOW)
    e.append("family", "test", {"value": 1}, NOW)
    with pytest.raises(ValueError, match="immutable"):
        e.append("family", "test", {"value": 2}, NOW)
    e.db.execute("UPDATE evidence SET body='{}'")
    with pytest.raises(ValueError, match="corrupt"):
        e.read("family", "test")
    e.close()


def test_day_blocks_include_no_trade_days_and_sample_threshold():
    assert not day_block_test([100] * 59, 0.001)["passes"]
    assert not day_block_test([-10] * 60, 0.001)["passes"]
    assert not day_block_test([0] * 59 + [100], 0.001)["passes"]
    result = day_block_test([100] * 60, 0.001)
    assert result["passes"] and result["p_value"] > 0


def test_resampling_resolution_tracks_repeated_look_alpha():
    alpha = 0.05 / (6 * 7 * 6 * 2)
    result = day_block_test([100.0] * 60, alpha)
    assert result["draws"] > 9999
    assert 1 / (result["draws"] + 1) <= alpha
    assert result["passes"]


def test_status_string_alone_never_grants_admission(tmp_path):
    s = Store(tmp_path / "paper.sqlite3")
    report = {
        "status": "paper_eligible",
        "strategy_id": "trend_pullback",
        "version": "forged",
    }
    s.put(
        "intraday_admission",
        {
            "trend_pullback": {
                "version": "forged",
                "report": report,
                "report_hash": digest(report),
            }
        },
    )
    assert not admitted(s, "trend_pullback", "forged")
    s.close()


def test_code_change_invalidates_frozen_version(monkeypatch):
    from quantpilot.paper.intraday import deployment
    from quantpilot.paper.intraday.strategy import SPECS

    prior = SPECS[0].version
    monkeypatch.setattr(
        deployment, "IMPLEMENTATION_HASH", "changed-executable-contract"
    )
    assert SPECS[0].version != prior


def test_admission_is_bound_to_validated_runtime_policy(tmp_path):
    from quantpilot.paper.intraday.deployment import VALIDATED_POLICY
    from quantpilot.paper.intraday.strategy import SPECS

    s = Store(tmp_path / "paper.sqlite3")
    values = {k: v for k, v in VALIDATED_POLICY.items() if k != "initial_capital"}
    s.configure(values | {"strategy_generation": "intraday_v2"}, 1)
    spec = SPECS[2]
    candidate = {"version": spec.version}
    report = {
        "status": "paper_eligible",
        "version": spec.version,
        "strategy_id": spec.strategy_id,
        "candidate_hash": digest(candidate),
        "historical_passed": True,
        "multiple_comparison_corrected": True,
        "shadow_days": 20,
        "shadow_round_trips": 1,
        "execution_policy": dict(VALIDATED_POLICY),
    }
    s.put(
        "intraday_admission",
        {
            spec.strategy_id: {
                "version": spec.version,
                "candidate": candidate,
                "report": report,
                "report_hash": digest(report),
            }
        },
    )
    assert admitted(s, spec.strategy_id, spec.version)
    for change in (
        {"fee_bps": 2},
        {"sell_tax_bps": 10},
        {"slippage_bps": 5},
        {"entry_cutoff_minutes": 35},
        {"liquidation_minutes": 15},
        {"trade_risk": 0.004},
    ):
        with pytest.raises(ValueError, match="admitted_execution_policy_immutable"):
            s.configure(change, s.policy.version)
    s.put("policy", s.policy.model_dump(mode="json") | {"fee_bps": 2})
    assert not admitted(s, spec.strategy_id, spec.version)
    s.close()


@pytest.mark.parametrize("capital", [5_000_000, 3_000_000])
def test_admit_boundary_requires_the_frozen_experiment_capital(tmp_path, capital):
    from quantpilot.paper.intraday.deployment import admit, VALIDATED_POLICY
    from quantpilot.paper.intraday.evaluation import encoded
    from quantpilot.paper.intraday.strategy import SPECS

    # Synthetic validated evidence tests the admission boundary, not market qualification.
    e = Experiment(tmp_path / "research.sqlite3")
    s = Store(tmp_path / "paper.sqlite3")
    if capital != 5_000_000:
        s.put("policy", s.policy.model_dump(mode="json") | {"initial_capital": capital})
        s.put("initial_capital", capital)
        s.put("cash", capital)
    spec = SPECS[2]
    candidate = {"strategy_id": spec.strategy_id, "version": spec.version}
    report = {
        "status": "paper_eligible",
        "strategy_id": spec.strategy_id,
        "version": spec.version,
        "candidate_hash": digest(candidate),
        "execution_policy": dict(VALIDATED_POLICY),
        "historical_passed": True,
        "multiple_comparison_corrected": True,
        "shadow_days": 20,
        "shadow_round_trips": 1,
    }
    e.append("frozen", spec.strategy_id, candidate, NOW)
    e.db.execute(
        "INSERT INTO looks(strategy,at,body) VALUES(?,?,?)",
        (spec.strategy_id, NOW.isoformat(), encoded(report)),
    )
    if capital == 5_000_000:
        admit(s, e, report, NOW)
        assert admitted(s, spec.strategy_id, spec.version)
        assert s.policy.slippage_bps == 10 and s.policy.max_positions == 2
    else:
        with pytest.raises(ValueError, match="validated_initial_capital_required"):
            admit(s, e, report, NOW)
        assert s.get("intraday_admission") is None
        assert s.policy.version == 1
    assert s.get("control") == "stopped" and s.orders() == []
    assert s.get("cash") == capital
    e.close()
    s.close()


def test_selection_is_frozen_before_any_holdout_and_fixture_never_qualifies(
    tmp_path, monkeypatch
):
    from datetime import timedelta
    from quantpilot.paper.intraday import evaluation
    from quantpilot.paper.intraday import replay as engine
    from quantpilot.paper.intraday.strategy import SPECS

    e = Experiment(tmp_path / "research.sqlite3")
    days = [(NOW - timedelta(days=180 - i)).date().isoformat() for i in range(180)]
    monkeypatch.setattr(evaluation, "trading_days", lambda *a: days)
    dataset = {
        "sha256": "fixture-hash",
        "data_mode": "fixture",
        "bars": [],
        "universes": [],
        "provenance": {},
    }

    def replay(data, spec, *, days, **kwargs):
        if len(days) == 60:
            assert len(e.records("frozen")) == 3
            assert len(e.records("trial")) == len(SPECS)
        return {
            "dataset_hash": data["sha256"],
            "version": spec.version,
            "net_pnl": 10000,
            "round_trips": 100,
            "trades": [{"r_multiple": 1}] * 100,
            "trading_days": days,
            "daily_pnl": {d: 100 for d in days},
            "unresolved": [],
            "quality_issues": [],
        }

    monkeypatch.setattr(engine, "replay", replay)
    result = evaluation.run_study(dataset, e, NOW)
    assert len(result["candidates"]) == 3
    report = evaluate_candidate(e, "trend_pullback", dataset["sha256"], NOW)
    assert report["status"] != "paper_eligible"
    assert "fixture_is_not_market_evidence" in report["reasons"]
    assert report["multiple_comparison_corrected"]
    assert set(report["sensitivity"]) == {
        "delay_2m",
        "half_fills",
        "no_fills",
        "gap_50bps",
    }
    e.close()
