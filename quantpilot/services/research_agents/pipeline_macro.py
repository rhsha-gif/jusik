"""Strategist team: macro-regime ∥ geopolitics → scenario writer ∥ independent refuter → editor.

The refuter deliberately receives only the two analysts' texts, never the
scenarios, so its refutations are not anchored on the writer's narrative;
the editor is the one who lines the two up. Any empty stage aborts the run.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from quantpilot.services.research_agents.models import AgentResult, MacroEvidenceBundle
from quantpilot.services.research_agents.prompts.macro import (
    EDITOR_SCHEMA,
    SCENARIO_SCHEMA,
    editor_prompt,
    geopolitics_prompt,
    macro_regime_prompt,
    refuter_prompt,
    scenario_prompt,
)
from quantpilot.services.research_agents.runner import AgentEmptyOutput, pool_workers, run_agent

MACRO_REGIME_AGENT = "qp-strat-macro-regime"
GEOPOLITICS_AGENT = "qp-strat-geopolitics"
SCENARIO_AGENT = "qp-strat-scenario-writer"
REFUTER_AGENT = "qp-strat-refuter"
EDITOR_AGENT = "qp-strat-editor"

Runner = Callable[..., AgentResult]


class MacroOutlookOutput(BaseModel):
    slack_text: str
    note_markdown: str
    scenarios: dict[str, Any] = Field(default_factory=dict)
    agent_results: list[AgentResult] = Field(default_factory=list)

    @property
    def generated_by(self) -> str:
        return ", ".join(f"{r.agent}/{r.model}" for r in self.agent_results)


def _validate_scenarios(payload: dict[str, Any] | None) -> dict[str, Any]:
    scenarios = (payload or {}).get("scenarios") or []
    if len(scenarios) < 2:
        raise AgentEmptyOutput(f"{SCENARIO_AGENT}: fewer than two scenarios returned")
    total = sum(float(s.get("probability", 0.0)) for s in scenarios)
    if not 0.9 <= total <= 1.1:
        raise AgentEmptyOutput(f"{SCENARIO_AGENT}: scenario probabilities sum to {total:.2f}, expected ~1.0")
    for s in scenarios:
        for field in ("question", "deadline", "resolution_source"):
            if not str(s.get(field, "")).strip():
                raise AgentEmptyOutput(f"{SCENARIO_AGENT}: scenario '{s.get('name', '?')}' lacks {field}")
    return payload or {}


def run_macro_pipeline(
    bundle: MacroEvidenceBundle,
    *,
    evidence_path: Path,
    cwd: str | Path,
    model: str,
    judge_model: str,
    runner: Runner = run_agent,
) -> MacroOutlookOutput:
    with ThreadPoolExecutor(max_workers=pool_workers()) as pool:
        macro_future = pool.submit(runner, MACRO_REGIME_AGENT, macro_regime_prompt(bundle), cwd=cwd, model=model)
        geo_future = pool.submit(runner, GEOPOLITICS_AGENT, geopolitics_prompt(bundle), cwd=cwd, model=model)
        macro = macro_future.result()
        geo = geo_future.result()

    with ThreadPoolExecutor(max_workers=pool_workers()) as pool:
        writer_future = pool.submit(
            runner, SCENARIO_AGENT, scenario_prompt(bundle, macro.text, geo.text), cwd=cwd, model=model, json_schema=SCENARIO_SCHEMA
        )
        refuter_future = pool.submit(runner, REFUTER_AGENT, refuter_prompt(bundle, macro.text, geo.text), cwd=cwd, model=judge_model)
        writer = writer_future.result()
        refuter = refuter_future.result()
    scenarios = _validate_scenarios(writer.json_output)

    editor = runner(
        EDITOR_AGENT,
        editor_prompt(bundle, macro.text, geo.text, scenarios, refuter.text, evidence_path),
        cwd=cwd,
        model=model,
        json_schema=EDITOR_SCHEMA,
    )
    output = editor.json_output or {}
    slack_text = str(output.get("slack_text", "")).strip()
    note_markdown = str(output.get("note_markdown", "")).strip()
    if not slack_text or not note_markdown:
        raise AgentEmptyOutput(f"{EDITOR_AGENT}: slack_text or note_markdown is empty")
    return MacroOutlookOutput(
        slack_text=slack_text,
        note_markdown=note_markdown,
        scenarios=scenarios,
        agent_results=[macro, geo, writer, refuter, editor],
    )
