from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from quantpilot.services.research_agents.models import AgentResult, EvidenceBundle
from quantpilot.services.research_agents.pipeline_market import (
    EDITOR_AGENT,
    MACRO_NEWS_AGENT,
    PRICE_FLOW_AGENT,
    run_market_pipeline,
)
from quantpilot.services.research_agents.runner import AgentEmptyOutput

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "research_agents"


def _bundle() -> EvidenceBundle:
    return EvidenceBundle.model_validate(
        {
            "date": "2026-09-03",
            "collected_at": "2026-09-03T16:10:00+09:00",
            "snapshot": {
                "date": "2026-09-03",
                "kospi_close": 3015.0,
                "kospi_change_pct": 1.17,
                "kosdaq_close": 900.0,
                "kosdaq_change_pct": -0.2,
                "sector_top": [{"name": "반도체", "change_pct": 3.4}],
                "sector_bottom": [{"name": "의약품", "change_pct": -1.9}],
                "investor_flows": {"foreign": 1200.0, "institution": -300.0, "individual": -900.0},
                "watchlist_rows": [
                    {"symbol": "005930", "name": "삼성전자", "theme": "반도체", "close": 71000.0, "change_pct": 2.1, "volume": 30000000, "volume_ratio_20d": 3.0}
                ],
            },
            "news": [
                {"id": "news:abc123def4", "title": "코스피 상승 마감", "link": "https://example-news.co.kr/1", "source_domain": "example-news.co.kr", "published_at": "2026-09-03T15:40:00+09:00", "query": "코스피"}
            ],
            "sources": [],
        }
    )


class FakeRunner:
    def __init__(self, *, empty_editor: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._empty_editor = empty_editor

    def __call__(self, agent: str, prompt: str, *, cwd: Any, model: str, json_schema: dict[str, Any] | None = None, **_: Any) -> AgentResult:
        self.calls.append({"agent": agent, "prompt": prompt, "model": model, "schema": json_schema})
        if agent == EDITOR_AGENT:
            structured = {} if self._empty_editor else {"slack_text": "📈 시황 2026-09-03\n코스피 3015.0", "note_markdown": "# 시황 2026-09-03\n## 출처\n- 증거"}
            return AgentResult(agent=agent, model=model, text=json.dumps(structured), json_output=structured, elapsed_s=1.0, exit_code=0)
        return AgentResult(agent=agent, model=model, text=f"## {agent} 출력\n코스피 3015.0", json_output=None, elapsed_s=1.0, exit_code=0)


def test_pipeline_runs_two_analysts_then_editor_with_schema(tmp_path: Path) -> None:
    runner = FakeRunner()
    evidence = tmp_path / "evidence_2026-09-03.json"
    out = run_market_pipeline(_bundle(), evidence_path=evidence, cwd=tmp_path, model="opus", runner=runner)

    agents = [c["agent"] for c in runner.calls]
    assert sorted(agents[:2]) == sorted([PRICE_FLOW_AGENT, MACRO_NEWS_AGENT])
    assert agents[2] == EDITOR_AGENT
    by_agent = {c["agent"]: c for c in runner.calls}
    assert '"kospi_close": 3015.0' in by_agent[PRICE_FLOW_AGENT]["prompt"]
    assert "news:abc123def4" in by_agent[MACRO_NEWS_AGENT]["prompt"]
    assert "삼성전자" in by_agent[MACRO_NEWS_AGENT]["prompt"]  # ensure_ascii=False
    editor_prompt = by_agent[EDITOR_AGENT]["prompt"]
    assert str(evidence) in editor_prompt and f"## {PRICE_FLOW_AGENT} 출력" in editor_prompt
    assert by_agent[EDITOR_AGENT]["schema"] is not None and "slack_text" in by_agent[EDITOR_AGENT]["schema"]["properties"]
    assert out.slack_text.startswith("📈 시황 2026-09-03")
    assert out.note_markdown.endswith("- 증거")
    assert out.generated_by.count("/opus") == 3


def test_pipeline_fails_when_editor_returns_empty_fields(tmp_path: Path) -> None:
    with pytest.raises(AgentEmptyOutput):
        run_market_pipeline(_bundle(), evidence_path=tmp_path / "e.json", cwd=tmp_path, model="opus", runner=FakeRunner(empty_editor=True))


def test_agent_definitions_exist_with_disallowed_tools_and_no_tools_allowlist() -> None:
    agents_dir = Path(__file__).resolve().parents[3] / ".claude" / "agents"
    for name in (PRICE_FLOW_AGENT, MACRO_NEWS_AGENT, EDITOR_AGENT):
        text = (agents_dir / f"{name}.md").read_text(encoding="utf-8")
        head = text.split("---")[1]
        assert f"name: {name}" in head
        assert "disallowedTools:" in head and "Write" in head and "Bash" in head
        assert "\ntools:" not in head
