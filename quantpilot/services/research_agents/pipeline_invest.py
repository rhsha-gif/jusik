"""Investment team: scout → per-candidate (base rate in code → researcher → refuter) → direction memo.

The output is a candidate note per symbol with `status: proposed`. Nothing here
touches QuantPilot's approval pool or any order code; the human decides with
`/invest-judge`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence

from pydantic import BaseModel, Field

from quantpilot.services.research_agents.analytics.base_rate import (
    Bar,
    above_sma,
    conditional_forward_returns,
    drawdown_from_high_at_most,
)
from quantpilot.services.research_agents.models import AgentResult, EvidenceBundle
from quantpilot.services.research_agents.prompts.invest import (
    SCOUT_SCHEMA,
    direction_prompt,
    refuter_prompt,
    researcher_prompt,
    scout_prompt,
)
from quantpilot.services.research_agents.publish.notes import read_open_decisions, read_recent_market_notes
from quantpilot.services.research_agents.runner import AgentEmptyOutput, run_agent

SCOUT_AGENT = "qp-invest-theme-scout"
RESEARCHER_AGENT = "qp-invest-stock-researcher"
REFUTER_AGENT = "qp-invest-refuter"
DIRECTION_AGENT = "qp-invest-portfolio-direction"

Runner = Callable[..., AgentResult]
History = Callable[[str], Sequence[Bar]]
SymbolValidator = Callable[[str], str | None]  # code -> name, or None when unknown


class Candidate(BaseModel):
    symbol: str
    name: str
    why: str = ""
    vault_citations: list[str] = Field(default_factory=list)
    news_ids: list[str] = Field(default_factory=list)


class CandidateResearch(BaseModel):
    candidate: Candidate
    base_rates: list[dict[str, Any]]
    researcher: AgentResult
    refuter: AgentResult
    note_markdown: str
    base_rate_path: str = ""


class InvestResearchOutput(BaseModel):
    candidates: list[CandidateResearch] = Field(default_factory=list)
    direction: AgentResult | None = None
    agent_results: list[AgentResult] = Field(default_factory=list)

    @property
    def generated_by(self) -> str:
        return ", ".join(f"{r.agent}/{r.model}" for r in self.agent_results)


def compute_base_rates(bars: Sequence[Bar]) -> list[dict[str, Any]]:
    """The two conditions the researcher always gets; more can be added without touching prompts."""

    if len(bars) < 30:
        return [{"condition": "insufficient history", "sessions": len(bars), "condition_hits": 0, "horizons": [], "sample_note": "fewer than 30 sessions — no base rate"}]
    return [
        conditional_forward_returns(bars, above_sma(200)).to_dict(),
        conditional_forward_returns(bars, drawdown_from_high_at_most(-20.0)).to_dict(),
    ]


def _scout(theme: str, bundle: EvidenceBundle, recent_notes: list[tuple[str, str]], watchlist: list[dict[str, str]], *, runner: Runner, cwd: Path, model: str) -> tuple[list[Candidate], AgentResult]:
    result = runner(SCOUT_AGENT, scout_prompt(theme, bundle, recent_notes, watchlist), cwd=cwd, model=model, json_schema=SCOUT_SCHEMA)
    raw = (result.json_output or {}).get("candidates") or []
    candidates = [Candidate.model_validate(item) for item in raw]
    if not candidates:
        raise AgentEmptyOutput(f"{SCOUT_AGENT}: no candidates returned")
    return candidates, result


def _assemble_note(research: CandidateResearch, direction_text: str, evidence_path: Path) -> str:
    c = research.candidate
    head = f"# 후보 {c.name} ({c.symbol})\n\n스카우트 근거: {c.why or '사용자 지정'}\n"
    sources = [f"- 증거 파일: {evidence_path}"]
    if research.base_rate_path:
        sources.append(f"- 기저율 파일: {research.base_rate_path}")
    sources += [f"- 기저율 조건: {b.get('condition')} ({b.get('sessions')} sessions)" for b in research.base_rates]
    sources += [f"- 볼트: {v}" for v in c.vault_citations]
    sources += [f"- 뉴스: {n}" for n in c.news_ids]
    return (
        head
        + "\n"
        + research.researcher.text.strip()
        + "\n\n## 반증 검토\n\n"
        + research.refuter.text.strip()
        + "\n\n## 방향 메모\n\n"
        + direction_text.strip()
        + "\n\n## 출처\n\n"
        + "\n".join(sources)
        + "\n"
    )


def run_invest_pipeline(
    *,
    theme: str | None,
    symbol: str | None,
    bundle: EvidenceBundle,
    evidence_path: Path,
    ledger_root: Path,
    cwd: str | Path,
    model: str,
    judge_model: str,
    history: History,
    symbol_validator: SymbolValidator,
    watchlist: list[dict[str, str]],
    runner: Runner = run_agent,
    max_candidates: int = 3,
) -> InvestResearchOutput:
    if bool(theme) == bool(symbol):
        raise ValueError("exactly one of theme or symbol is required")
    cwd_path = Path(cwd)
    recent_notes = read_recent_market_notes(5, root=ledger_root)
    results: list[AgentResult] = []

    if symbol:
        name = symbol_validator(symbol)
        if name is None:
            raise ValueError(f"unknown symbol: {symbol}")
        candidates = [Candidate(symbol=symbol, name=name, why="사용자 지정")]
    else:
        proposed, scout_result = _scout(theme or "", bundle, recent_notes, watchlist, runner=runner, cwd=cwd_path, model=model)
        results.append(scout_result)
        candidates = []
        for cand in proposed:
            if symbol_validator(cand.symbol) is None:
                continue
            candidates.append(cand)
            if len(candidates) >= max_candidates:
                break
        if not candidates:
            raise AgentEmptyOutput(f"{SCOUT_AGENT}: every proposed symbol failed validation")

    researched: list[CandidateResearch] = []
    for cand in candidates:
        base_rates = compute_base_rates(history(cand.symbol))
        # written before the agents run so the refuter can open and verify the numbers (measured gap, 2026-09-04)
        base_rate_path = evidence_path.parent / f"base_rate_{bundle.date}_{cand.symbol}.json"
        base_rate_path.parent.mkdir(parents=True, exist_ok=True)
        base_rate_path.write_text(json.dumps(base_rates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        researcher = runner(RESEARCHER_AGENT, researcher_prompt(cand.model_dump(), bundle, base_rates, evidence_path, base_rate_path), cwd=cwd_path, model=model)
        refuter = runner(REFUTER_AGENT, refuter_prompt(cand.model_dump(), researcher.text, bundle, base_rate_path), cwd=cwd_path, model=judge_model)
        results += [researcher, refuter]
        researched.append(CandidateResearch(candidate=cand, base_rates=base_rates, researcher=researcher, refuter=refuter, note_markdown="", base_rate_path=str(base_rate_path)))

    summaries = [f"{r.candidate.name} ({r.candidate.symbol}): {r.candidate.why[:120]}" for r in researched]
    direction = runner(
        DIRECTION_AGENT,
        direction_prompt(read_open_decisions(root=ledger_root), recent_notes, summaries, bundle),
        cwd=cwd_path,
        model=model,
    )
    results.append(direction)
    for research in researched:
        research.note_markdown = _assemble_note(research, direction.text, evidence_path)
    return InvestResearchOutput(candidates=researched, direction=direction, agent_results=results)
