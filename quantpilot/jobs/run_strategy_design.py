"""Strategy design: hypothesis → market structure → designer → code-validated recipe → code backtest → forensics ∥ risk gate → note.

Exit codes: 0 ok, 2 data/collection failed, 3 an agent produced nothing or
the recipe failed validation twice, 4 publishing failed. Manual only (the
hypothesis is the input). Research-only: the recipe lands in
`quantpilot/docs/strategy_specs/` as `promotion_status: draft` and the note in
the private ledger; nothing here touches promotion or order code.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from quantpilot.packages.core.data.providers import build_providers
from quantpilot.packages.core.schemas import DataMode
from quantpilot.packages.core.strategies.loader import default_strategy_dir
from quantpilot.services.api.dependencies import validate_generic_runtime_environment
from quantpilot.services.research_agents.publish.notes import NoteExistsError, write_research_note
from quantpilot.services.research_agents.runner import AgentEmptyOutput, AgentRunError
from quantpilot.services.strategy_design.analytics.market_structure import (
    build_market_structure,
    evidence_to_json,
    load_securities,
)
from quantpilot.services.strategy_design.pipeline import DesignOutput, RecipeRejected, run_design_pipeline

EXIT_OK = 0
EXIT_COLLECTION = 2
EXIT_AGENT = 3
EXIT_PUBLISH = 4
DEFAULT_OUT_DIR = Path(".research_agents_out")
DEFAULT_MODEL = os.environ.get("QUANTPILOT_RESEARCH_MODEL", "opus")
DEFAULT_JUDGE_MODEL = os.environ.get("QUANTPILOT_RESEARCH_JUDGE_MODEL", "fable")
NOTE_TYPE = "strategy-design"
_REPO_ROOT = Path(__file__).resolve().parents[2]

log = logging.getLogger("quantpilot.strategy_design")


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--date must be YYYY-MM-DD, got {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Designer team run for one strategy hypothesis (research only).")
    parser.add_argument("--hypothesis", required=True, help="one-line strategy hypothesis, in Korean or English")
    parser.add_argument("--date", type=_iso_date, default=date.today().isoformat(), help="session date YYYY-MM-DD (default: today)")
    parser.add_argument("--data-dir", default=os.environ.get("LOCAL_DATA_DIR", "local_data"), help="directory with securities.csv/ohlcv.csv")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="evidence and run logs")
    parser.add_argument("--strategy-dir", default=None, help="where the recipe YAML is written (default: quantpilot/docs/strategy_specs)")
    parser.add_argument("--dry-run", action="store_true", help="compute market-structure evidence only; no agents")
    parser.add_argument("--no-post", action="store_true", help="run everything but write no ledger note")
    parser.add_argument("--force", action="store_true", help="overwrite an existing recipe file / ledger note")
    parser.add_argument("--max-position-weight", type=float, default=0.15)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model for the structure analyst and the designer")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, help="model for forensics and the risk gate")
    return parser


def _setup_logging(out_dir: Path, session_date: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(out_dir / f"run_design_{session_date}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log.handlers.clear()
    log.addHandler(handler)
    log.addHandler(stream)
    log.setLevel(logging.INFO)


def _load_history(data_dir: Path) -> tuple[list[dict[str, Any]], Any]:
    _, provider = build_providers(DataMode.local_historical, data_dir=data_dir)
    return provider.get_price_history(), provider


def run(
    argv: Sequence[str] | None = None,
    *,
    history_loader: Callable[[Path], tuple[list[dict[str, Any]], Any]] = _load_history,
    pipeline: Callable[..., DesignOutput] = run_design_pipeline,
    note_writer: Callable[..., Path] = write_research_note,
    validate_env: Callable[[], object] = validate_generic_runtime_environment,
) -> int:
    args = build_parser().parse_args(argv)
    validate_env()
    out_dir = Path(args.out_dir)
    _setup_logging(out_dir, args.date)
    data_dir = Path(args.data_dir)
    strategy_dir = Path(args.strategy_dir) if args.strategy_dir else default_strategy_dir()

    try:
        history, provider = history_loader(data_dir)
        securities_path = data_dir / "securities.csv"
        securities = load_securities(securities_path) if securities_path.exists() else {}
        evidence = build_market_structure(history, as_of=args.date if history and any(str(r.get("date")) <= args.date for r in history) else None, securities=securities)
    except Exception as exc:  # provider errors are typed per source; the job reports any of them as a collection failure
        log.error("data or market-structure computation failed: %s: %s", type(exc).__name__, exc)
        return EXIT_COLLECTION
    evidence_json = evidence_to_json(evidence)
    evidence_path = out_dir / f"evidence_structure_{args.date}.json"
    evidence_path.write_text(evidence_json + "\n", encoding="utf-8")
    log.info("market structure evidence written: %s (universe %d, as_of %s)", evidence_path, evidence.universe_size, evidence.as_of)
    if args.dry_run:
        return EXIT_OK

    try:
        output = pipeline(
            hypothesis=args.hypothesis,
            session_date=args.date,
            price_history=history,
            market_data_provider=provider,
            structure_evidence_json=evidence_json,
            evidence_path=evidence_path.resolve(),
            strategy_dir=strategy_dir,
            cwd=_REPO_ROOT,
            model=args.model,
            judge_model=args.judge_model,
            max_position_weight=args.max_position_weight,
            force=args.force,
        )
    except RecipeRejected as exc:
        log.error("recipe rejected after retry: %s", "; ".join(exc.errors))
        return EXIT_AGENT
    except AgentEmptyOutput as exc:
        log.error("agent produced nothing: %s", exc)
        return EXIT_AGENT
    except AgentRunError as exc:
        log.error("agent run failed: %s", exc)
        return EXIT_AGENT
    for result in output.agent_results:
        log.info("agent %s model=%s elapsed=%.1fs", result.agent, result.model, result.elapsed_s)
    report_path = out_dir / f"backtest_{output.recipe['strategy_id']}_{args.date}.json"
    report_path.write_text(json.dumps({"report": output.report.model_dump(mode="json"), "forensics": output.forensics, "risk": output.risk}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("recipe %s; forensics=%s/%s risk=%s; report %s", output.recipe_path, output.forensics.get("overall_confidence"), output.forensics.get("recommended_action"), output.risk.get("verdict"), report_path)
    if args.no_post:
        preview = out_dir / f"design_note_{output.recipe['strategy_id']}_{args.date}.md"
        preview.write_text(output.note_markdown + "\n", encoding="utf-8")
        log.info("no-post: note not written to the ledger (preview: %s)", preview)
        return EXIT_OK

    try:
        note_path = note_writer(
            args.date,
            f"strategy-{output.recipe['strategy_id']}",
            output.note_markdown,
            note_type=NOTE_TYPE,
            evidence_path=evidence_path,
            generated_by=output.generated_by,
            extra_fields={
                "strategy_id": str(output.recipe["strategy_id"]),
                "recipe": str(Path(output.recipe_path).resolve()),
                "forensics": str(output.forensics.get("overall_confidence")),
                "risk_gate": str(output.risk.get("verdict")),
                "promotion_status": "draft",
            },
            force=args.force,
        )
        log.info("ledger note written: %s", note_path)
    except NoteExistsError as exc:
        log.error("%s (use --force to overwrite)", exc)
        return EXIT_PUBLISH
    return EXIT_OK


def main() -> int:
    started = datetime.now()
    code = run()
    log.info("finished with exit %d in %.0fs", code, (datetime.now() - started).total_seconds())
    return code


if __name__ == "__main__":
    raise SystemExit(main())
