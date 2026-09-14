from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from quantpilot.services.research_agents.models import AgentResult, MacroEvidenceBundle
from quantpilot.services.research_agents.pipeline_macro import (
    EDITOR_AGENT,
    GEOPOLITICS_AGENT,
    MACRO_REGIME_AGENT,
    REFUTER_AGENT,
    SCENARIO_AGENT,
    run_macro_pipeline,
)
from quantpilot.services.research_agents.runner import AgentEmptyOutput

SCENARIO = {
    "name": "완만한 둔화",
    "probability": 0.55,
    "thesis": "수출 YoY 둔화가 이어진다.",
    "supporting_precedent": "2019년",
    "countervailing_precedent": "2023년",
    "observable_change_factor": "수출 YoY (관세청)",
    "question": "2026-12 수출 YoY가 0% 아래인가",
    "deadline": "2026-12-31",
    "resolution_source": "관세청 월간 수출입 동향",
    "invalidation": {"id": "S1-I1", "threshold": "수출 YoY > +5%", "cadence": "월간", "source": "관세청"},
    "korea_exposure": "반도체 수출주",
}


def _bundle() -> MacroEvidenceBundle:
    return MacroEvidenceBundle.model_validate(
        {
            "date": "2026-09-13",
            "collected_at": "2026-09-13T20:00:00+09:00",
            "macro": {
                "as_of": "2026-09-13",
                "collected_at": "2026-09-13T20:00:00+09:00",
                "series": [
                    {"id": "kr_base_rate", "label": "기준금리", "source": "ecos", "code": "722Y001/0101000", "cycle": "M", "unit": "%", "role": "policy", "verified": False, "points": [{"time": "2026-08", "value": 2.5}], "latest": 2.5, "latest_time": "2026-08"}
                ],
                "regime": {"quadrant": "Q4_growth_down_inflation_down", "growth_score": -0.8, "inflation_score": -0.3, "growth_inputs": ["kr_exports"], "inflation_inputs": ["kr_cpi"], "method": "m"},
                "gpr": {"as_of_month": "2026-08", "gpr": 130.2, "gpr_percentile_10y": 71.0, "gpr_korea": 0.9, "citation": "Caldara & Iacoviello"},
                "skipped": ["fred: FRED_API_KEY not set"],
                "sources": [],
            },
            "news": [
                {"id": "news:abc123def4", "title": "연준, 금리 동결", "link": "https://example-news.co.kr/1", "source_domain": "example-news.co.kr", "published_at": "2026-09-12T10:00:00+09:00", "query": "연준 금리"}
            ],
            "open_decisions": ["2026-09-10-usd-etf-preconversion"],
        }
    )


class FakeRunner:
    def __init__(self, *, scenarios: list[dict[str, Any]] | None = None, empty_editor: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._scenarios = [SCENARIO, {**SCENARIO, "name": "재가속", "probability": 0.45}] if scenarios is None else scenarios
        self._empty_editor = empty_editor

    def __call__(self, agent: str, prompt: str, *, cwd: Any, model: str, json_schema: dict[str, Any] | None = None, **_: Any) -> AgentResult:
        self.calls.append({"agent": agent, "prompt": prompt, "model": model, "schema": json_schema})
        if agent == SCENARIO_AGENT:
            structured = {"regime_summary": "Q4", "base_case": "완만한 둔화", "scenarios": self._scenarios}
            return AgentResult(agent=agent, model=model, text=json.dumps(structured), json_output=structured, elapsed_s=1.0, exit_code=0)
        if agent == EDITOR_AGENT:
            structured = {} if self._empty_editor else {"slack_text": "🧭 매크로 전망 2026-09-13\n레짐 Q4", "note_markdown": "# 매크로 전망 2026-09-13\n## 출처\n- 증거"}
            return AgentResult(agent=agent, model=model, text=json.dumps(structured), json_output=structured, elapsed_s=1.0, exit_code=0)
        return AgentResult(agent=agent, model=model, text=f"## {agent} 출력\n기준금리 2.5", json_output=None, elapsed_s=1.0, exit_code=0)


def test_pipeline_order_models_and_refuter_independence(tmp_path: Path) -> None:
    runner = FakeRunner()
    evidence = tmp_path / "evidence_macro_2026-09-13.json"
    out = run_macro_pipeline(_bundle(), evidence_path=evidence, cwd=tmp_path, model="opus", judge_model="fable", runner=runner)

    agents = [c["agent"] for c in runner.calls]
    assert sorted(agents[:2]) == sorted([MACRO_REGIME_AGENT, GEOPOLITICS_AGENT])
    assert sorted(agents[2:4]) == sorted([SCENARIO_AGENT, REFUTER_AGENT])
    assert agents[4] == EDITOR_AGENT
    by_agent = {c["agent"]: c for c in runner.calls}
    assert by_agent[REFUTER_AGENT]["model"] == "fable" and by_agent[SCENARIO_AGENT]["model"] == "opus"
    # the refuter never sees the scenarios; the editor sees everything
    assert "완만한 둔화" not in by_agent[REFUTER_AGENT]["prompt"] and "시나리오는 일부러" in by_agent[REFUTER_AGENT]["prompt"]
    assert "완만한 둔화" in by_agent[EDITOR_AGENT]["prompt"] and str(evidence) in by_agent[EDITOR_AGENT]["prompt"]
    # analysts get the evidence numbers, without the raw point arrays
    assert '"quadrant": "Q4_growth_down_inflation_down"' in by_agent[MACRO_REGIME_AGENT]["prompt"]
    assert '"points"' not in by_agent[MACRO_REGIME_AGENT]["prompt"]
    assert "news:abc123def4" in by_agent[GEOPOLITICS_AGENT]["prompt"] and '"gpr_korea": 0.9' in by_agent[GEOPOLITICS_AGENT]["prompt"]
    assert "2026-09-10-usd-etf-preconversion" in by_agent[SCENARIO_AGENT]["prompt"]
    assert by_agent[SCENARIO_AGENT]["schema"]["properties"]["scenarios"]["minItems"] == 2
    assert out.slack_text.startswith("🧭 매크로 전망") and out.scenarios["base_case"] == "완만한 둔화"
    assert out.generated_by.count("/fable") == 1 and out.generated_by.count("/opus") == 4


@pytest.mark.parametrize(
    "scenarios",
    [
        [SCENARIO],  # fewer than two
        [SCENARIO, {**SCENARIO, "probability": 0.9}],  # sums to 1.45
        [SCENARIO, {**SCENARIO, "probability": 0.45, "deadline": ""}],  # missing deadline
    ],
)
def test_pipeline_rejects_unresolvable_scenarios(tmp_path: Path, scenarios: list[dict[str, Any]]) -> None:
    with pytest.raises(AgentEmptyOutput):
        run_macro_pipeline(_bundle(), evidence_path=tmp_path / "e.json", cwd=tmp_path, model="opus", judge_model="fable", runner=FakeRunner(scenarios=scenarios))


def test_pipeline_fails_when_editor_returns_empty_fields(tmp_path: Path) -> None:
    with pytest.raises(AgentEmptyOutput):
        run_macro_pipeline(_bundle(), evidence_path=tmp_path / "e.json", cwd=tmp_path, model="opus", judge_model="fable", runner=FakeRunner(empty_editor=True))


def test_strategist_agent_definitions_exist_with_deny_list_and_no_allowlist() -> None:
    agents_dir = Path(__file__).resolve().parents[3] / ".claude" / "agents"
    for name in (MACRO_REGIME_AGENT, GEOPOLITICS_AGENT, SCENARIO_AGENT, REFUTER_AGENT, EDITOR_AGENT):
        text = (agents_dir / f"{name}.md").read_text(encoding="utf-8")
        head = text.split("---")[1]
        assert f"name: {name}" in head
        assert "disallowedTools:" in head and "Write" in head and "Bash" in head
        assert "\ntools:" not in head
