from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from quantpilot.services.research_agents.models import AgentResult
from quantpilot.services.research_agents.pipeline_invest import (
    DIRECTION_AGENT,
    REFUTER_AGENT,
    RESEARCHER_AGENT,
    SCOUT_AGENT,
    compute_base_rates,
    run_invest_pipeline,
)
from quantpilot.services.research_agents.publish.notes import read_open_decisions, write_market_note
from quantpilot.services.research_agents.runner import AgentEmptyOutput
from quantpilot.tests.unit.test_research_pipeline_market import _bundle

_WATCHLIST = [{"code": "005930", "name": "삼성전자", "theme": "반도체"}]
_KNOWN = {"005930": "삼성전자", "000660": "SK하이닉스"}


def _bars(n: int = 300) -> list[dict[str, Any]]:
    return [{"date": f"d{i}", "close": 100.0 + (i % 7), "volume": 1} for i in range(n)]


class FakeRunner:
    def __init__(self, scout_candidates: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._scout = scout_candidates

    def __call__(self, agent: str, prompt: str, *, cwd: Any, model: str, json_schema: dict[str, Any] | None = None, **_: Any) -> AgentResult:
        self.calls.append({"agent": agent, "prompt": prompt, "model": model, "schema": json_schema})
        if agent == SCOUT_AGENT:
            structured = {"candidates": self._scout or []}
            return AgentResult(agent=agent, model=model, text=json.dumps(structured), json_output=structured, elapsed_s=1.0, exit_code=0)
        return AgentResult(agent=agent, model=model, text=f"## {agent}\n본문", json_output=None, elapsed_s=1.0, exit_code=0)


def _run(tmp_path: Path, runner: FakeRunner, **kwargs: Any):
    params: dict[str, Any] = dict(
        theme=None,
        symbol=None,
        bundle=_bundle(),
        evidence_path=tmp_path / "evidence.json",
        ledger_root=tmp_path,
        cwd=tmp_path,
        model="opus",
        judge_model="fable",
        history=lambda s: _bars(),
        symbol_validator=lambda s: _KNOWN.get(s),
        watchlist=_WATCHLIST,
        runner=runner,
    )
    params.update(kwargs)
    return run_invest_pipeline(**params)


def test_symbol_path_skips_scout_and_runs_researcher_refuter_direction(tmp_path: Path) -> None:
    runner = FakeRunner()
    out = _run(tmp_path, runner, symbol="005930")

    agents = [c["agent"] for c in runner.calls]
    assert agents == [RESEARCHER_AGENT, REFUTER_AGENT, DIRECTION_AGENT]
    by_agent = {c["agent"]: c for c in runner.calls}
    assert by_agent[REFUTER_AGENT]["model"] == "fable" and by_agent[RESEARCHER_AGENT]["model"] == "opus"
    assert '"condition": "close > SMA200"' in by_agent[RESEARCHER_AGENT]["prompt"]
    base_rate_file = tmp_path / "base_rate_2026-09-03_005930.json"
    assert base_rate_file.exists() and str(base_rate_file) in by_agent[RESEARCHER_AGENT]["prompt"]
    assert str(base_rate_file) in by_agent[REFUTER_AGENT]["prompt"]
    assert str(base_rate_file) in out.candidates[0].note_markdown
    assert f"## {RESEARCHER_AGENT}" in by_agent[REFUTER_AGENT]["prompt"]
    note = out.candidates[0].note_markdown
    assert note.startswith("# 후보 삼성전자 (005930)")
    for section in ("## 반증 검토", "## 방향 메모", "## 출처"):
        assert section in note
    assert "close > SMA200" in note and str(tmp_path / "evidence.json") in note


def test_theme_path_validates_symbols_and_caps_candidates(tmp_path: Path) -> None:
    runner = FakeRunner(
        scout_candidates=[
            {"symbol": "000660", "name": "SK하이닉스", "why": "HBM", "vault_citations": ["[[팩터]]"], "news_ids": ["news:1"]},
            {"symbol": "999999", "name": "없는회사", "why": "x", "vault_citations": [], "news_ids": []},
            {"symbol": "005930", "name": "삼성전자", "why": "관심종목", "vault_citations": [], "news_ids": []},
        ]
    )
    out = _run(tmp_path, runner, theme="AI 메모리", max_candidates=1)

    assert runner.calls[0]["agent"] == SCOUT_AGENT and runner.calls[0]["schema"] is not None
    assert [c.candidate.symbol for c in out.candidates] == ["000660"]
    assert "[[팩터]]" in out.candidates[0].note_markdown and "news:1" in out.candidates[0].note_markdown
    assert [c["agent"] for c in runner.calls].count(RESEARCHER_AGENT) == 1


def test_scout_with_no_valid_candidates_is_an_empty_output(tmp_path: Path) -> None:
    with pytest.raises(AgentEmptyOutput):
        _run(tmp_path, FakeRunner(scout_candidates=[{"symbol": "999999", "name": "x", "why": "", "vault_citations": [], "news_ids": []}]), theme="t")
    with pytest.raises(ValueError):
        _run(tmp_path, FakeRunner(), theme="t", symbol="005930")
    with pytest.raises(ValueError, match="unknown symbol"):
        _run(tmp_path, FakeRunner(), symbol="999999")


def test_direction_prompt_receives_open_decisions_and_recent_notes(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions"
    decisions.mkdir()
    (decisions / "2026-08-12-btc-dca.md").write_text(
        "---\ndecision_id: 2026-08-12-btc-dca\nasset: BTC\nstatus: open\n---\n\n## 사실\n비밀 금액 999\n\n## 무효화 조건\n- T-Q6 거래대금\n\n## 실행 규칙\n매월\n",
        encoding="utf-8",
    )
    (decisions / "old.md").write_text("---\nstatus: superseded\n---\n## 무효화 조건\nx\n", encoding="utf-8")
    write_market_note("2026-09-02", "# 시황 2026-09-02\n내용", evidence_path=tmp_path / "e.json", generated_by="x", root=tmp_path)

    opened = read_open_decisions(root=tmp_path)
    assert [name for name, _ in opened] == ["2026-08-12-btc-dca"]
    assert "T-Q6" in opened[0][1] and "비밀 금액" not in opened[0][1] and "매월" not in opened[0][1]

    runner = FakeRunner()
    _run(tmp_path, runner, symbol="005930")
    direction_prompt = [c for c in runner.calls if c["agent"] == DIRECTION_AGENT][0]["prompt"]
    assert "2026-08-12-btc-dca" in direction_prompt and "T-Q6" in direction_prompt
    assert "시황 2026-09-02" in direction_prompt
    assert "비밀 금액" not in direction_prompt


def test_base_rates_need_history() -> None:
    short = compute_base_rates(_bars(10))
    assert short[0]["condition"] == "insufficient history"
    full = compute_base_rates(_bars(400))
    assert [r["condition"] for r in full] == ["close > SMA200", "drawdown from 250-session high <= -20.0%"]


def test_invest_agent_definitions_exist() -> None:
    agents_dir = Path(__file__).resolve().parents[3] / ".claude" / "agents"
    for name in (SCOUT_AGENT, RESEARCHER_AGENT, REFUTER_AGENT, DIRECTION_AGENT):
        head = (agents_dir / f"{name}.md").read_text(encoding="utf-8").split("---")[1]
        assert f"name: {name}" in head and "disallowedTools:" in head and "\ntools:" not in head
