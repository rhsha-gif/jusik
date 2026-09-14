"""Designer team: market structure → strategy designer → (code) recipe validation + backtest → forensics ∥ risk gate → note.

The recipe the designer returns is assembled by code into a `StrategyRecipe`
with the safety fields pinned (`promotion_status: draft`, no execution
levels, limit orders only), validated by the pydantic contract and the rule
grammar, and only then backtested by code. Promotion stays a human act
(`packages/core/strategies/promotion.py`); nothing here can grant it.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, Field, ValidationError

from quantpilot.packages.core.backtest.costs import cost_basis_label, kis_retail_assumptions
from quantpilot.packages.core.backtest.schemas import BacktestAssumptions
from quantpilot.packages.core.schemas import StrategyRecipe
from quantpilot.services.research_agents.models import AgentResult
from quantpilot.services.research_agents.runner import AgentEmptyOutput, pool_workers, run_agent
from quantpilot.services.strategy_design.backtest_runner import (
    BacktestReport,
    append_trial,
    read_trials,
    run_recipe_backtest,
    trial_entry,
)
from quantpilot.services.strategy_design.prompts import (
    DESIGN_SCHEMA,
    FORENSICS_SCHEMA,
    RISK_SCHEMA,
    designer_prompt,
    forensics_prompt,
    market_structure_prompt,
    risk_gate_prompt,
)
from quantpilot.services.strategy_design.rule_engine import GRAMMAR_HELP, validate_recipe_rules

MARKET_STRUCTURE_AGENT = "qp-design-market-structure"
DESIGNER_AGENT = "qp-design-strategy-designer"
FORENSICS_AGENT = "qp-design-backtest-forensics"
RISK_GATE_AGENT = "qp-design-risk-gate"

RESERVED_IDS = {"pullback_trend_v1", "pullback_trend_v2"}
_ID_RE = re.compile(r"^[a-z0-9_]{3,64}$")

Runner = Callable[..., AgentResult]


class DesignOutput(BaseModel):
    recipe_path: str
    recipe: dict[str, Any]
    report: BacktestReport
    forensics: dict[str, Any]
    risk: dict[str, Any]
    note_markdown: str
    agent_results: list[AgentResult] = Field(default_factory=list)

    @property
    def generated_by(self) -> str:
        return ", ".join(f"{r.agent}/{r.model}" for r in self.agent_results)


class RecipeRejected(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def assemble_recipe(draft: dict[str, Any], *, session_date: str) -> dict[str, Any]:
    """Designer JSON → StrategyRecipe dict with the safety fields pinned by code, never by the model."""

    risk_matrix = draft.get("risk_matrix") or {}
    return {
        "strategy_id": str(draft.get("strategy_id", "")).strip(),
        "version": str(draft.get("version", "0.1")).strip() or "0.1",
        "description": str(draft.get("description", "")).strip(),
        "market": "KR_STOCK",
        "timeframe": "daily",
        "universe_filter": dict(draft.get("universe_filter") or {}),
        "features": list(draft.get("features") or []),
        "entry_rules": list(draft.get("entry_rules") or []),
        "exit_rules": list(draft.get("exit_rules") or []),
        "no_chasing_rules": {"max_premium_over_reference": 0.005, "max_reproposals_per_day": 1},
        "position_sizing": dict(draft.get("position_sizing") or {}),
        "risk_rules": list(draft.get("risk_rules") or []),
        "rebalance": str(draft.get("rebalance", "weekly")),
        "execution_permissions": {"allowed_order_types": ["limit"], "market_orders": "disabled"},
        "validation": dict(draft.get("validation") or {}),
        "promotion_status": "draft",
        "allowed_execution_levels": [],
        "audit_metadata": {
            "authored_by": DESIGNER_AGENT,
            "authored_at": session_date,
            "hypothesis": str(draft.get("hypothesis", "")),
            "design_notes": str(draft.get("design_notes", "")),
            "sources": list(draft.get("sources") or []),
            "risk_matrix": risk_matrix,
            "reviewed_by": "qp-design-backtest-forensics, qp-design-risk-gate (research only)",
        },
        "status": "draft",
    }


def validate_recipe(data: dict[str, Any], *, existing_ids: set[str]) -> tuple[StrategyRecipe | None, list[str]]:
    errors: list[str] = []
    sid = str(data.get("strategy_id", ""))
    if not _ID_RE.match(sid):
        errors.append(f"strategy_id '{sid}' must match [a-z0-9_]{{3,64}}")
    if sid in RESERVED_IDS or sid in existing_ids:
        errors.append(f"strategy_id '{sid}' already exists; choose a new id")
    try:
        recipe = StrategyRecipe.model_validate(data)
    except ValidationError as exc:
        for item in exc.errors():
            loc = ".".join(str(p) for p in item.get("loc", ()))
            errors.append(f"{loc}: {item.get('msg')}")
        return None, errors
    errors += validate_recipe_rules(recipe)
    wf = (recipe.validation or {}).get("walk_forward") or {}
    max_lookback = max([int(f.get("lookback_days", 0)) for f in recipe.features] + [0])
    if int(wf.get("purge_bars", 0)) < max_lookback and max_lookback > 0:
        errors.append(f"validation.walk_forward.purge_bars ({wf.get('purge_bars', 0)}) must be >= the longest feature lookback ({max_lookback})")
    return (recipe if not errors else None), errors


def _vault_citations(*texts: str) -> list[str]:
    found: list[str] = []
    for text in texts:
        for match in re.findall(r"\[\[([^\]]+)\]\]", text or ""):
            if match not in found:
                found.append(match)
    return found


def assemble_note(
    *,
    session_date: str,
    hypothesis: str,
    structure_text: str,
    recipe: dict[str, Any],
    recipe_path: Path,
    report: BacktestReport,
    forensics: dict[str, Any],
    risk: dict[str, Any],
    evidence_path: Path,
) -> str:
    m = report.metrics
    s = report.statistics
    feat = "\n".join(f"| {f.get('name')} | `{f.get('formula')}` | {f.get('lookback_days')} | {f.get('source_citation')} |" for f in recipe.get("features", []))
    entry = "\n".join(f"- `{r}`" for r in recipe.get("entry_rules", []))
    exit_ = "\n".join(f"- `{r}`" for r in recipe.get("exit_rules", []))
    wf = "\n".join(
        f"| {w.window_id} | {w.train_days} | {w.test_start}~{w.test_end} | {w.signals} | {w.filled_trades} | {w.total_return} | {w.max_drawdown} | {w.sharpe_per_period} |"
        for w in report.walk_forward
    )
    findings = "\n".join(f"- [{f.get('severity')}/{f.get('category')}] {f.get('finding')} → {f.get('remediation')}" for f in forensics.get("findings", [])) or "- 발견 없음"
    checks = "\n".join(f"- [{c.get('status')}] {c.get('name')}: 관측 {c.get('observed')} / 기준 {c.get('threshold')} — {c.get('detail')}" for c in risk.get("checks", [])) or "- 검사 없음"
    sources = "\n".join(
        f"- [{src.get('grade')}{'' if src.get('verified') else ', 미검증'}] {src.get('title')} ({src.get('year')}) — {src.get('url_or_doi')} — {src.get('key_claim')}"
        for src in (recipe.get("audit_metadata") or {}).get("sources", [])
    )
    vault = "\n".join(f"- [[{v}]]" for v in _vault_citations(structure_text, forensics.get("notes", ""), risk.get("notes", "")))
    return (
        f"# 전략 설계 {recipe.get('strategy_id')} v{recipe.get('version')} ({session_date})\n\n"
        f"가설: {hypothesis}\n\n"
        f"판정: 포렌식 `{forensics.get('overall_confidence')}` / 권고 `{forensics.get('recommended_action')}` · 리스크 게이트 `{risk.get('verdict')}` · "
        f"승격 상태 `draft` (승격은 사람이 `promotion.py`로만)\n\n"
        "## 시장구조\n\n" + structure_text.strip() + "\n\n"
        f"## 레시피\n\n경로: `{recipe_path}`\n\n설계 메모: {(recipe.get('audit_metadata') or {}).get('design_notes', '')}\n\n"
        "| 피처 | 식 | 룩백 | 출처 |\n|---|---|---|---|\n" + feat + "\n\n"
        "진입 규칙\n" + entry + "\n\n청산 규칙\n" + exit_ + "\n\n"
        f"사이징: `{json.dumps(recipe.get('position_sizing'), ensure_ascii=False)}` · 리밸런스: {recipe.get('rebalance')}\n\n"
        "## 백테스트 (코드 계산)\n\n"
        f"기간 {report.start_date}~{report.end_date}, 신호 {report.signals_generated}건, 체결 {report.trades.get('filled')} / 차단 {report.trades.get('blocked')}\n\n"
        "| 총수익 | 연환산 | MDD | 변동성 | 단순 샤프 | 회전율 | 적중률 |\n|---|---|---|---|---|---|---|\n"
        f"| {m.get('total_return')} | {m.get('annualized_return')} | {m.get('max_drawdown')} | {m.get('volatility')} | {m.get('simplified_sharpe')} | {m.get('turnover')} | {m.get('hit_rate')} |\n\n"
        "| 기간당 샤프 | n | 왜도 | 첨도 | PSR | DSR | 기대 최대 SR | 시행 수 | MinTRL(95%) | 분산 출처 |\n|---|---|---|---|---|---|---|---|---|---|\n"
        f"| {s.get('sharpe_per_period')} | {s.get('n')} | {s.get('skew')} | {s.get('kurtosis')} | {s.get('psr')} | {s.get('dsr')} | {s.get('expected_max_sr')} | {s.get('n_trials')} | {s.get('min_trl_95')} | {s.get('variance_source')} |\n\n"
        f"워크포워드 {json.dumps(report.walk_forward_config)}\n\n"
        "| 창 | 학습 봉(purge 후) | 테스트 구간 | 신호 | 체결 | 총수익 | MDD | 기간당 샤프 |\n|---|---|---|---|---|---|---|---|\n" + (wf or "| (창 없음) | | | | | | | |") + "\n\n"
        f"비용 기준: {cost_basis_label(BacktestAssumptions.model_validate(report.assumptions))} (수수료 {report.assumptions.get('fee_bps')}bp, 슬리피지 {report.assumptions.get('slippage_bps')}bp, 매도세 {report.assumptions.get('sell_tax_bps')}bp)\n\n"
        "엔진 한계\n" + "\n".join(f"- {e}" for e in report.engine_limits) + "\n\n"
        "## 포렌식 판정\n\n" + findings + f"\n\n{forensics.get('notes', '')}\n\n"
        "## 리스크 게이트\n\n" + checks + ("\n\n차단 사유\n" + "\n".join(f"- {r}" for r in risk.get("blocking_reasons", [])) if risk.get("blocking_reasons") else "") + f"\n\n{risk.get('notes', '')}\n\n"
        "## 출처\n\n"
        f"- 증거 파일: {evidence_path}\n- 레시피: {recipe_path}\n" + (sources + "\n" if sources else "") + (vault + "\n" if vault else "")
    )


def run_design_pipeline(
    *,
    hypothesis: str,
    session_date: str,
    price_history: list[dict[str, Any]],
    market_data_provider: Any,
    structure_evidence_json: str,
    evidence_path: Path,
    strategy_dir: Path,
    cwd: str | Path,
    model: str,
    judge_model: str,
    runner: Runner = run_agent,
    assumptions: BacktestAssumptions | None = None,
    max_position_weight: float = 0.15,
    force: bool = False,
    write_recipe: bool = True,
) -> DesignOutput:
    cwd_path = Path(cwd)
    results: list[AgentResult] = []
    structure = runner(MARKET_STRUCTURE_AGENT, market_structure_prompt(session_date, structure_evidence_json), cwd=cwd_path, model=model)
    results.append(structure)

    existing_ids = {p.stem for p in strategy_dir.glob("*.yaml")} if strategy_dir.exists() else set()
    errors: list[str] = []
    previous: dict[str, Any] | None = None
    recipe: StrategyRecipe | None = None
    data: dict[str, Any] = {}
    for attempt in range(2):
        prompt = designer_prompt(session_date, hypothesis, structure.text, GRAMMAR_HELP, existing_ids=sorted(existing_ids | RESERVED_IDS), retry_errors=errors or None, previous=previous)
        designer = runner(DESIGNER_AGENT, prompt, cwd=cwd_path, model=model, json_schema=DESIGN_SCHEMA)
        results.append(designer)
        draft = designer.json_output or {}
        if not draft:
            raise AgentEmptyOutput(f"{DESIGNER_AGENT}: no structured output")
        data = assemble_recipe(draft, session_date=session_date)
        recipe, errors = validate_recipe(data, existing_ids=existing_ids if not force else set())
        if recipe is not None:
            break
        previous = draft
    if recipe is None:
        raise RecipeRejected(errors)

    recipe_path = strategy_dir / f"{recipe.strategy_id}.yaml"
    if write_recipe:
        if recipe_path.exists() and not force:
            raise RecipeRejected([f"recipe file already exists: {recipe_path}"])
        strategy_dir.mkdir(parents=True, exist_ok=True)
        recipe_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")

    trials_path = strategy_dir / ".trials.jsonl"
    trials = read_trials(trials_path)
    trial_sharpes = [float(t["sharpe_per_period"]) for t in trials if t.get("sharpe_per_period") is not None]
    report = run_recipe_backtest(
        recipe,
        price_history,
        market_data_provider,
        assumptions=assumptions or kis_retail_assumptions(allow_fractional_shares=False),
        max_position_weight=max_position_weight,
        trials_so_far=len(trials),
        trial_sharpes=trial_sharpes or None,
    )
    if write_recipe:
        append_trial(trials_path, trial_entry(recipe, report, hypothesis))

    report_dict = report.model_dump(mode="json")
    with ThreadPoolExecutor(max_workers=pool_workers()) as pool:
        forensics_future = pool.submit(runner, FORENSICS_AGENT, forensics_prompt(session_date, data, report_dict), cwd=cwd_path, model=judge_model, json_schema=FORENSICS_SCHEMA)
        risk_future = pool.submit(runner, RISK_GATE_AGENT, risk_gate_prompt(session_date, data, report_dict["metrics"] | {"trades": report_dict["trades"]}), cwd=cwd_path, model=judge_model, json_schema=RISK_SCHEMA)
        forensics_result = forensics_future.result()
        risk_result = risk_future.result()
    results += [forensics_result, risk_result]
    forensics = forensics_result.json_output or {}
    risk = risk_result.json_output or {}
    if not forensics.get("overall_confidence") or not risk.get("verdict"):
        raise AgentEmptyOutput("forensics or risk gate returned no verdict")

    note = assemble_note(
        session_date=session_date,
        hypothesis=hypothesis,
        structure_text=structure.text,
        recipe=data,
        recipe_path=recipe_path,
        report=report,
        forensics=forensics,
        risk=risk,
        evidence_path=evidence_path,
    )
    return DesignOutput(recipe_path=str(recipe_path), recipe=data, report=report, forensics=forensics, risk=risk, note_markdown=note, agent_results=results)
