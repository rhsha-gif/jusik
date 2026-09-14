from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from quantpilot.jobs import run_strategy_design as job
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.services.research_agents.models import AgentResult
from quantpilot.services.research_agents.publish.notes import write_research_note
from quantpilot.services.strategy_design.pipeline import (
    DESIGNER_AGENT,
    FORENSICS_AGENT,
    MARKET_STRUCTURE_AGENT,
    RISK_GATE_AGENT,
    RecipeRejected,
    assemble_recipe,
    run_design_pipeline,
    validate_recipe,
)

VALID_DRAFT: dict[str, Any] = {
    "strategy_id": "trend_momo_test",
    "version": "0.1",
    "description": "추세 위 모멘텀 진입, 추세 이탈 청산",
    "hypothesis": "추세 위에서 10일 모멘텀이 양수면 이어진다",
    "design_notes": "브레드스가 낮아 20일 추세 필터를 넣었다",
    "universe_filter": {"min_avg_daily_value": 5000000, "max_universe_size": 50},
    "features": [
        {"name": "trend", "formula": "close > sma(close, 20)", "lookback_days": 20, "source_citation": "Moskowitz et al. (2012) [미검증]"},
        {"name": "momo", "formula": "ret(close, 10)", "lookback_days": 10, "source_citation": "Jegadeesh & Titman (1993) [미검증]"},
    ],
    "entry_rules": ["trend and momo > 0.02"],
    "exit_rules": ["close < sma(close, 20)"],
    "position_sizing": {"method": "capped_target_weight", "max_target_weight": 0.1},
    "risk_rules": ["limit orders only", "respect policy max position weight"],
    "rebalance": "weekly",
    "validation": {"walk_forward": {"train_size": 60, "test_size": 20, "purge_bars": 20, "embargo_bars": 2}},
    "sources": [
        {"id": "s1", "type": "peer_reviewed", "title": "Time series momentum", "year": 2012, "url_or_doi": "10.1016/j.jfineco.2011.11.003", "key_claim": "12개월 모멘텀 지속", "grade": "B", "verified": False, "challenges_hypothesis": False},
        {"id": "s2", "type": "working_paper", "title": "Momentum crashes", "year": 2016, "url_or_doi": "10.1016/j.jfineco.2016.01.002", "key_claim": "반등 국면에서 모멘텀 붕괴", "grade": "B", "verified": False, "challenges_hypothesis": True},
    ],
    "risk_matrix": {"sizing_formula": "fixed_fraction", "max_position_pct": 10, "max_sector_pct": 30, "max_portfolio_drawdown_pct": 20, "stop_loss_pct": 8, "correlation_budget": 0.5, "leverage_max": 1.0, "circuit_breakers": [{"trigger": "portfolio_drawdown_exceeds_10pct", "action": "halt_new_entries"}, {"trigger": "position_loss_exceeds_stop_loss", "action": "close_position"}], "sources": ["Kelly (1956)"]},
}


def _bars(n: int = 300, symbols: tuple[str, ...] = ("000001", "000002", "000003")) -> list[dict[str, Any]]:
    rng = random.Random(7)
    rows: list[dict[str, Any]] = []
    start = date(2025, 1, 6)
    for k, symbol in enumerate(symbols):
        price = 10000.0 * (k + 1)
        d = start
        for _ in range(n):
            while d.weekday() >= 5:
                d += timedelta(days=1)
            drift = 0.0015 if k == 0 else (-0.0008 if k == 1 else 0.0)
            price *= 1 + drift + rng.gauss(0, 0.012)
            high = price * (1 + abs(rng.gauss(0, 0.005)))
            low = price * (1 - abs(rng.gauss(0, 0.005)))
            rows.append({"symbol": symbol, "date": d.isoformat(), "open": round(price * (1 + rng.gauss(0, 0.002)), 2), "high": round(high, 2), "low": round(low, 2), "close": round(price, 2), "volume": int(1_000_000 + rng.random() * 500_000)})
            d += timedelta(days=1)
    return rows


class FakeRunner:
    def __init__(self, drafts: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._drafts = list(drafts) if drafts is not None else [VALID_DRAFT]

    def __call__(self, agent: str, prompt: str, *, cwd: Any, model: str, json_schema: dict[str, Any] | None = None, **_: Any) -> AgentResult:
        self.calls.append({"agent": agent, "prompt": prompt, "model": model, "schema": json_schema})
        if agent == DESIGNER_AGENT:
            draft = self._drafts.pop(0) if self._drafts else VALID_DRAFT
            return AgentResult(agent=agent, model=model, text=json.dumps(draft), json_output=draft, elapsed_s=1.0, exit_code=0)
        if agent == FORENSICS_AGENT:
            payload = {"overall_confidence": "medium", "findings": [{"category": "engine_limit", "finding": "KRX ±30% 미모델링", "severity": "info", "remediation": "없음"}], "deflated_sharpe_quoted": None, "recommended_action": "revise", "notes": "표본 2년 [[예측과 캘리브레이션]]"}
            return AgentResult(agent=agent, model=model, text=json.dumps(payload), json_output=payload, elapsed_s=1.0, exit_code=0)
        if agent == RISK_GATE_AGENT:
            payload = {"verdict": "pass", "checks": [{"name": "mdd_vs_position", "status": "pass", "observed": "20", "threshold": ">= 20", "detail": "ok"}], "blocking_reasons": [], "notes": ""}
            return AgentResult(agent=agent, model=model, text=json.dumps(payload), json_output=payload, elapsed_s=1.0, exit_code=0)
        return AgentResult(agent=agent, model=model, text=f"## 시장 레짐\n{agent} 출력 [[브레드스]]", json_output=None, elapsed_s=1.0, exit_code=0)


def _run(tmp_path: Path, runner: FakeRunner, **kwargs: Any):
    bars = _bars()
    return run_design_pipeline(
        hypothesis="추세 위 모멘텀",
        session_date="2026-09-14",
        price_history=bars,
        market_data_provider=bars,
        structure_evidence_json='{"universe_size": 3}',
        evidence_path=tmp_path / "evidence_structure.json",
        strategy_dir=tmp_path / "specs",
        cwd=tmp_path,
        model="opus",
        judge_model="fable",
        runner=runner,
        **kwargs,
    )


def test_pipeline_validates_writes_recipe_backtests_and_judges(tmp_path: Path) -> None:
    runner = FakeRunner()
    out = _run(tmp_path, runner)

    agents = [c["agent"] for c in runner.calls]
    assert agents[:2] == [MARKET_STRUCTURE_AGENT, DESIGNER_AGENT] and sorted(agents[2:]) == sorted([FORENSICS_AGENT, RISK_GATE_AGENT])
    by_agent = {c["agent"]: c for c in runner.calls}
    assert by_agent[FORENSICS_AGENT]["model"] == "fable" and by_agent[DESIGNER_AGENT]["model"] == "opus"
    assert "sma(" in by_agent[DESIGNER_AGENT]["prompt"] and "pullback_trend_v2" in by_agent[DESIGNER_AGENT]["prompt"]
    assert '"statistics"' in by_agent[FORENSICS_AGENT]["prompt"] and '"n_trials": 1' in by_agent[FORENSICS_AGENT]["prompt"]
    assert '"risk_matrix"' in by_agent[RISK_GATE_AGENT]["prompt"]

    recipe = load_strategy_recipe("trend_momo_test", strategy_dir=tmp_path / "specs")
    assert recipe.promotion_status == "draft" and recipe.allowed_execution_levels == [] and recipe.execution_permissions["market_orders"] == "disabled"
    assert recipe.audit_metadata["authored_by"] == DESIGNER_AGENT and recipe.audit_metadata["risk_matrix"]["max_position_pct"] == 10
    assert out.report.signals_generated > 0 and out.report.trades["filled"] > 0
    assert set(out.report.statistics) >= {"sharpe_per_period", "psr", "dsr", "n_trials", "min_trl_95", "variance_source"}
    assert out.report.walk_forward_config == {"train_size": 60, "test_size": 20, "purge_bars": 20, "embargo_bars": 2} and out.report.walk_forward
    assert out.report.assumptions["allow_fractional_shares"] is False
    assert out.report.assumptions["sell_tax_bps"] == 20.0 and out.report.assumptions["fee_bps"] == 1.40527  # KIS retail cost basis, not the engine default
    assert out.report.walk_forward[0].train_days == 40 and out.report.walk_forward[0].test_days == 20  # 60 train minus 20 purge
    assert out.report.data_provenance.get("source") and "비용 기준" in out.note_markdown
    trials = (tmp_path / "specs" / ".trials.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(trials) == 1 and json.loads(trials[0])["strategy_id"] == "trend_momo_test"
    assert "# 전략 설계 trend_momo_test" in out.note_markdown and "## 포렌식 판정" in out.note_markdown and "[[브레드스]]" in out.note_markdown and "[[예측과 캘리브레이션]]" in out.note_markdown
    assert "승격은 사람이" in out.note_markdown
    assert out.generated_by.count("/fable") == 2


def test_second_run_counts_prior_trial_and_refuses_overwrite(tmp_path: Path) -> None:
    _run(tmp_path, FakeRunner())
    with pytest.raises(RecipeRejected):
        _run(tmp_path, FakeRunner())
    out = _run(tmp_path, FakeRunner([{**VALID_DRAFT, "strategy_id": "trend_momo_test_b"}]))
    assert out.report.statistics["n_trials"] == 2 and out.report.statistics["variance_source"] == "trials_ledger"


def test_invalid_draft_is_retried_once_with_errors_then_rejected(tmp_path: Path) -> None:
    prose = {**VALID_DRAFT, "entry_rules": ["pullback_rsi recovers from oversold zone"]}
    runner = FakeRunner([prose, VALID_DRAFT])
    out = _run(tmp_path, runner)
    designer_calls = [c for c in runner.calls if c["agent"] == DESIGNER_AGENT]
    assert len(designer_calls) == 2 and "재시도" in designer_calls[1]["prompt"] and "recovers" in designer_calls[1]["prompt"]
    assert out.recipe["strategy_id"] == "trend_momo_test"

    with pytest.raises(RecipeRejected) as excinfo:
        _run(tmp_path, FakeRunner([prose, {**VALID_DRAFT, "strategy_id": "pullback_trend_v2"}]))
    assert any("already exists" in e for e in excinfo.value.errors)


def test_validate_recipe_pins_safety_fields_and_purge_floor() -> None:
    data = assemble_recipe(VALID_DRAFT, session_date="2026-09-14")
    assert data["promotion_status"] == "draft" and data["allowed_execution_levels"] == [] and data["execution_permissions"]["allowed_order_types"] == ["limit"]
    recipe, errors = validate_recipe(data, existing_ids=set())
    assert recipe is not None and errors == []
    shallow = assemble_recipe({**VALID_DRAFT, "validation": {"walk_forward": {"train_size": 60, "test_size": 20, "purge_bars": 5, "embargo_bars": 0}}}, session_date="2026-09-14")
    _, errors = validate_recipe(shallow, existing_ids=set())
    assert any("purge_bars" in e for e in errors)
    tampered = assemble_recipe({**VALID_DRAFT, "position_sizing": {"method": "kelly_full", "max_target_weight": 0.1}}, session_date="2026-09-14")
    _, errors = validate_recipe(tampered, existing_ids=set())
    assert any("position_sizing" in e for e in errors)


def test_job_dry_run_and_full_run(tmp_path: Path) -> None:
    bars = _bars()
    ledger = tmp_path / "ledger"
    written: list[Path] = []

    def fake_pipeline(**kwargs: Any):
        runner = FakeRunner()
        return run_design_pipeline(**{**kwargs, "runner": runner})

    code = job.run(
        ["--hypothesis", "h", "--date", "2026-09-14", "--data-dir", str(tmp_path / "nodata"), "--out-dir", str(tmp_path / "out"), "--dry-run"],
        history_loader=lambda p: (bars, bars),
        validate_env=lambda: None,
    )
    assert code == job.EXIT_OK and (tmp_path / "out" / "evidence_structure_2026-09-14.json").exists()

    code = job.run(
        ["--hypothesis", "추세 위 모멘텀", "--date", "2026-09-14", "--data-dir", str(tmp_path / "nodata"), "--out-dir", str(tmp_path / "out"), "--strategy-dir", str(tmp_path / "specs")],
        history_loader=lambda p: (bars, bars),
        pipeline=fake_pipeline,
        note_writer=lambda *a, **k: written.append(write_research_note(*a, root=ledger, **k)) or written[-1],
        validate_env=lambda: None,
    )
    assert code == job.EXIT_OK and written and written[0].name == "2026-09-14-strategy-trend_momo_test.md"
    text = written[0].read_text(encoding="utf-8")
    assert "type: strategy-design" in text and "promotion_status: draft" in text and "risk_gate: pass" in text
    assert (tmp_path / "out" / "backtest_trend_momo_test_2026-09-14.json").exists()


def test_designer_agent_definitions_exist_with_deny_list() -> None:
    agents_dir = Path(__file__).resolve().parents[3] / ".claude" / "agents"
    for name in (MARKET_STRUCTURE_AGENT, DESIGNER_AGENT, FORENSICS_AGENT, RISK_GATE_AGENT):
        head = (agents_dir / f"{name}.md").read_text(encoding="utf-8").split("---")[1]
        assert f"name: {name}" in head and "disallowedTools:" in head and "Bash" in head and "\ntools:" not in head
    for legacy in ("quant-recipe-architect", "backtest-forensics-agent", "risk-gatekeeper-agent", "source-curator-agent"):
        assert not (agents_dir / f"{legacy}.md").exists(), f"absorbed legacy agent still installed: {legacy}"
