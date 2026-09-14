"""Weekly macro outlook: ECOS/FRED/GPR + headlines → strategist team → ledger research note → Slack.

Exit codes: 0 ok, 2 collection failed, 3 an agent produced nothing, 4
publishing failed. Runs on any day (the scheduled slot is Sunday evening).
Keys (`ECOS_API_KEY`, `FRED_API_KEY`, news, Slack) come from the process
environment only; a missing macro key skips that source and the note says so.
`--list-items <STAT_CODE>` prints an ECOS item table for verifying the codes
in `config/macro_series.json` once a key is issued.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from quantpilot.services.api.dependencies import validate_generic_runtime_environment
from quantpilot.services.research_agents.collectors.ecos import ECOS_KEY_ENV, EcosClient, MacroCollectionError
from quantpilot.services.research_agents.collectors.macro import collect_macro
from quantpilot.services.research_agents.collectors.naver_news import NewsCollectionError, collect_news
from quantpilot.services.research_agents.models import MacroEvidence, MacroEvidenceBundle, NewsItem
from quantpilot.services.research_agents.pipeline_macro import MacroOutlookOutput, run_macro_pipeline
from quantpilot.services.research_agents.publish.notes import NoteExistsError, read_open_decisions, write_research_note
from quantpilot.services.research_agents.publish.slack import SlackPostError, post
from quantpilot.services.research_agents.runner import AgentEmptyOutput, AgentRunError

EXIT_OK = 0
EXIT_COLLECTION = 2
EXIT_AGENT = 3
EXIT_PUBLISH = 4
DEFAULT_OUT_DIR = Path(".research_agents_out")
DEFAULT_MODEL = os.environ.get("QUANTPILOT_RESEARCH_MODEL", "opus")
DEFAULT_JUDGE_MODEL = os.environ.get("QUANTPILOT_RESEARCH_JUDGE_MODEL", "fable")
NOTE_TYPE = "macro-outlook"
NOTE_SLUG = "macro-outlook"
MACRO_QUERIES = ("연준 금리", "미국 관세", "중동 정세", "북한", "중국 경기", "반도체 수출", "환율 원달러")
_REPO_ROOT = Path(__file__).resolve().parents[2]

log = logging.getLogger("quantpilot.macro_outlook")


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--date must be YYYY-MM-DD, got {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weekly macro/geopolitics outlook (research only, never a trading input).")
    parser.add_argument("--date", type=_iso_date, default=date.today().isoformat(), help="session date YYYY-MM-DD (default: today)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="evidence and run logs")
    parser.add_argument("--dry-run", action="store_true", help="collect and write evidence only; no agents, no publishing")
    parser.add_argument("--no-post", action="store_true", help="run the agents but write nothing to the ledger or Slack")
    parser.add_argument("--force", action="store_true", help="overwrite an existing ledger note for the day")
    parser.add_argument("--skip-news", action="store_true", help="no Naver news; the outlook says so")
    parser.add_argument("--skip-macro", action="store_true", help="no ECOS/FRED/GPR collection; the regime is reported as undetermined")
    parser.add_argument("--no-slack", action="store_true", help="write the ledger note but do not post to Slack")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model for analysts, writer and editor")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, help="model for the independent refuter")
    parser.add_argument("--list-items", metavar="STAT_CODE", help="print the ECOS item list for a stat code and exit (needs ECOS_API_KEY)")
    return parser


def _setup_logging(out_dir: Path, session_date: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(out_dir / f"run_macro_{session_date}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log.handlers.clear()
    log.addHandler(handler)
    log.addHandler(stream)
    log.setLevel(logging.INFO)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _list_items(stat_code: str, environ: dict[str, str]) -> int:
    key = environ.get(ECOS_KEY_ENV, "")
    if not key:
        log.error("%s not set", ECOS_KEY_ENV)
        return EXIT_COLLECTION
    try:
        rows = EcosClient(key).items(stat_code)
    except MacroCollectionError as exc:
        log.error("ecos item list failed: %s", exc)
        return EXIT_COLLECTION
    for row in rows:
        print("\t".join(row.get(k, "") for k in ("ITEM_CODE", "ITEM_NAME", "CYCLE", "START_TIME", "END_TIME", "UNIT_NAME")))
    return EXIT_OK


def evidence_path(out_dir: Path, session_date: str) -> Path:
    return out_dir / f"evidence_macro_{session_date}.json"


def write_bundle(bundle: MacroEvidenceBundle, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_path(out_dir, bundle.date)
    path.write_text(json.dumps(bundle.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def run(
    argv: Sequence[str] | None = None,
    *,
    macro_collector: Callable[..., MacroEvidence] = collect_macro,
    news_client_factory: Callable[[], object] | None = None,
    pipeline: Callable[..., MacroOutlookOutput] = run_macro_pipeline,
    poster: Callable[..., object] = post,
    note_writer: Callable[..., Path] = write_research_note,
    validate_env: Callable[[], object] = validate_generic_runtime_environment,
    open_decisions: Callable[[], list[tuple[str, str]]] = read_open_decisions,
    environ: dict[str, str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    validate_env()
    env = dict(os.environ) if environ is None else environ
    out_dir = Path(args.out_dir)
    _setup_logging(out_dir, args.date)
    if args.list_items:
        return _list_items(args.list_items, env)

    stamp = _now_iso()
    if args.skip_macro:
        macro = MacroEvidence(as_of=args.date, collected_at=stamp, skipped=["macro collection skipped (--skip-macro)"])
        log.warning("macro skipped (--skip-macro): the regime will be undetermined")
    else:
        macro = macro_collector(as_of=date.fromisoformat(args.date), repo_root=_REPO_ROOT, environ=env, collected_at=stamp)
        log.info("macro collected: %d series, %d skipped", len(macro.series), len(macro.skipped))

    news: list[NewsItem] = []
    if args.skip_news:
        log.warning("news skipped (--skip-news): the outlook will carry no headlines")
    else:
        try:
            news_client = (news_client_factory or _default_news_client)()
            news = collect_news(news_client, list(MACRO_QUERIES))  # type: ignore[arg-type]
        except NewsCollectionError as exc:
            log.error("news collection failed: %s", exc)
            return EXIT_COLLECTION
    try:
        decision_ids = [decision_id for decision_id, _ in open_decisions()]
    except OSError as exc:
        log.warning("could not read open decisions: %s", exc)
        decision_ids = []

    bundle = MacroEvidenceBundle(date=args.date, collected_at=stamp, macro=macro, news=news, open_decisions=decision_ids)
    path = write_bundle(bundle, out_dir)
    log.info("evidence written: %s (%d news items, regime=%s)", path, len(news), macro.regime.quadrant if macro.regime else "n/a")
    if args.dry_run:
        return EXIT_OK

    try:
        output = pipeline(bundle, evidence_path=path.resolve(), cwd=_REPO_ROOT, model=args.model, judge_model=args.judge_model)
    except AgentEmptyOutput as exc:
        log.error("agent produced nothing: %s", exc)
        return EXIT_AGENT
    except AgentRunError as exc:
        log.error("agent run failed: %s", exc)
        return EXIT_AGENT
    for result in output.agent_results:
        log.info("agent %s model=%s elapsed=%.1fs", result.agent, result.model, result.elapsed_s)
    scenarios_path = out_dir / f"scenarios_{args.date}.json"
    scenarios_path.write_text(json.dumps(output.scenarios, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.no_post:
        preview = out_dir / f"outlook_{args.date}.md"
        preview.write_text(output.note_markdown + "\n\n---\n\n" + output.slack_text + "\n", encoding="utf-8")
        log.info("no-post: outlook generated but not published (scenarios: %s, preview: %s)", scenarios_path, preview)
        return EXIT_OK

    try:
        note_path = note_writer(
            args.date,
            NOTE_SLUG,
            output.note_markdown,
            note_type=NOTE_TYPE,
            evidence_path=path,
            generated_by=output.generated_by,
            extra_fields={"scenarios": str(scenarios_path.resolve()), "regime": macro.regime.quadrant if macro.regime else "n/a"},
            force=args.force,
        )
        log.info("ledger note written: %s", note_path)
    except NoteExistsError as exc:
        log.error("%s (use --force to overwrite)", exc)
        return EXIT_PUBLISH
    if args.no_slack:
        log.info("no-slack: ledger note written, Slack skipped")
        return EXIT_OK
    try:
        scrub_result = poster(output.slack_text)
        log.info("slack posted (scrub replacements: %s)", getattr(scrub_result, "replaced", 0))
    except SlackPostError as exc:
        log.error("slack post failed: %s", exc)
        return EXIT_PUBLISH
    return EXIT_OK


def _default_news_client() -> object:
    from quantpilot.services.research_agents.collectors.naver_news import NaverNewsClient

    return NaverNewsClient()


def main() -> int:
    started = datetime.now()
    code = run()
    log.info("finished with exit %d in %.0fs", code, (datetime.now() - started).total_seconds())
    return code


if __name__ == "__main__":
    raise SystemExit(main())
