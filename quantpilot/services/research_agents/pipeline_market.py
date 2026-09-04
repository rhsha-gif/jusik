"""Market team: two analysts in parallel, then the editor.

Any empty stage output aborts the run — a brief with a missing half is worse
than a missing brief because it looks complete.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from quantpilot.services.research_agents.models import AgentResult, EvidenceBundle
from quantpilot.services.research_agents.prompts.market import (
    EDITOR_SCHEMA,
    editor_prompt,
    macro_news_prompt,
    price_flow_prompt,
)
from quantpilot.services.research_agents.runner import AgentEmptyOutput, run_agent

PRICE_FLOW_AGENT = "qp-market-price-flow-analyst"
MACRO_NEWS_AGENT = "qp-market-macro-news-analyst"
EDITOR_AGENT = "qp-market-editor"

Runner = Callable[..., AgentResult]


class MarketBriefOutput(BaseModel):
    slack_text: str
    note_markdown: str
    agent_results: list[AgentResult] = Field(default_factory=list)

    @property
    def generated_by(self) -> str:
        return ", ".join(f"{r.agent}/{r.model}" for r in self.agent_results)


def run_market_pipeline(
    bundle: EvidenceBundle,
    *,
    evidence_path: Path,
    cwd: str | Path,
    model: str,
    runner: Runner = run_agent,
) -> MarketBriefOutput:
    with ThreadPoolExecutor(max_workers=2) as pool:
        price_future = pool.submit(runner, PRICE_FLOW_AGENT, price_flow_prompt(bundle), cwd=cwd, model=model)
        news_future = pool.submit(runner, MACRO_NEWS_AGENT, macro_news_prompt(bundle), cwd=cwd, model=model)
        price = price_future.result()
        news = news_future.result()

    editor = runner(
        EDITOR_AGENT,
        editor_prompt(bundle, price.text, news.text, evidence_path),
        cwd=cwd,
        model=model,
        json_schema=EDITOR_SCHEMA,
    )
    output = editor.json_output or {}
    slack_text = str(output.get("slack_text", "")).strip()
    note_markdown = str(output.get("note_markdown", "")).strip()
    if not slack_text or not note_markdown:
        raise AgentEmptyOutput(f"{EDITOR_AGENT}: slack_text or note_markdown is empty")
    return MarketBriefOutput(slack_text=slack_text, note_markdown=note_markdown, agent_results=[price, news, editor])
