"""Daily market brief: collect → evidence → market team → ledger note → Slack.

Exit codes: 0 ok (or closed day), 2 collection failed, 3 an agent produced
nothing, 4 publishing failed. The note is written before the Slack post so a
webhook failure still leaves the brief in the ledger. Credentials are read
from the environment only; this job never opens `.env`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Sequence

from quantpilot.services.api.dependencies import validate_generic_runtime_environment
from quantpilot.services.research_agents.collectors.evidence import build_evidence, write_evidence
from quantpilot.services.research_agents.collectors.krx import CollectionError, collect_market_snapshot, load_watchlist
from quantpilot.services.research_agents.collectors.naver_news import (
    DEFAULT_QUERIES,
    NewsCollectionError,
    collect_news,
)
from quantpilot.services.research_agents.pipeline_market import MarketBriefOutput, run_market_pipeline
from quantpilot.services.research_agents.publish.notes import NoteExistsError, write_market_note
from quantpilot.services.research_agents.publish.slack import SlackPostError, post_webhook
from quantpilot.services.research_agents.runner import AgentEmptyOutput, AgentRunError

EXIT_OK = 0
EXIT_COLLECTION = 2
EXIT_AGENT = 3
EXIT_PUBLISH = 4
DEFAULT_OUT_DIR = Path(".research_agents_out")
DEFAULT_MODEL = os.environ.get("QUANTPILOT_RESEARCH_MODEL", "opus")
_REPO_ROOT = Path(__file__).resolve().parents[2]

log = logging.getLogger("quantpilot.market_brief")


def is_closed_day(session_date: date, holidays_env: str | None = None) -> bool:
    if session_date.weekday() >= 5:
        return True
    raw = os.environ.get("KRX_HOLIDAYS", "") if holidays_env is None else holidays_env
    return session_date.isoformat() in {item.strip() for item in raw.split(",") if item.strip()}


def _iso_date(value: str) -> str:
    """argparse type: a canonical YYYY-MM-DD string or an error (file names derive from it)."""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--date must be YYYY-MM-DD, got {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily KRX market brief (research only, never a trading input).")
    parser.add_argument("--date", type=_iso_date, default=date.today().isoformat(), help="KRX session date YYYY-MM-DD (default: today)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="evidence and run logs")
    parser.add_argument("--dry-run", action="store_true", help="collect and write evidence only; no agents, no publishing")
    parser.add_argument("--no-post", action="store_true", help="run the agents but write nothing to the ledger or Slack")
    parser.add_argument("--force", action="store_true", help="overwrite an existing ledger note for the day")
    parser.add_argument("--skip-news", action="store_true", help="no Naver news (no credentials yet); the brief says so")
    parser.add_argument("--no-slack", action="store_true", help="write the ledger note but do not post to Slack")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model for the market team agents")
    return parser


def _setup_logging(out_dir: Path, session_date: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(out_dir / f"run_{session_date}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log.handlers.clear()
    log.addHandler(handler)
    log.addHandler(stream)
    log.setLevel(logging.INFO)


def run(
    argv: Sequence[str] | None = None,
    *,
    krx_client_factory: Callable[[], object] | None = None,
    news_client_factory: Callable[[], object] | None = None,
    pipeline: Callable[..., MarketBriefOutput] = run_market_pipeline,
    poster: Callable[..., object] = post_webhook,
    note_writer: Callable[..., Path] = write_market_note,
    validate_env: Callable[[], object] = validate_generic_runtime_environment,
    watchlist_loader: Callable[[], list] = load_watchlist,
) -> int:
    args = build_parser().parse_args(argv)
    validate_env()
    out_dir = Path(args.out_dir)
    _setup_logging(out_dir, args.date)
    session = date.fromisoformat(args.date)
    if is_closed_day(session):
        log.info("closed day %s — nothing to do", args.date)
        return EXIT_OK

    try:
        krx_client = (krx_client_factory or _default_krx_client)()
        watchlist = watchlist_loader()
        snapshot = collect_market_snapshot(args.date, krx_client, watchlist)  # type: ignore[arg-type]
        if args.skip_news:
            news = []
            log.warning("news skipped (--skip-news): the brief will carry no headlines")
        else:
            news_client = (news_client_factory or _default_news_client)()
            queries = [entry.name for entry in watchlist] + list(DEFAULT_QUERIES)
            news = collect_news(news_client, queries)  # type: ignore[arg-type]
    except (CollectionError, NewsCollectionError) as exc:
        log.error("collection failed: %s", exc)
        return EXIT_COLLECTION
    bundle = build_evidence(args.date, snapshot, news)
    evidence_path = write_evidence(bundle, out_dir)
    log.info("evidence written: %s (%d news items)", evidence_path, len(news))
    if args.dry_run:
        return EXIT_OK

    try:
        output = pipeline(bundle, evidence_path=evidence_path.resolve(), cwd=_REPO_ROOT, model=args.model)
    except AgentEmptyOutput as exc:
        log.error("agent produced nothing: %s", exc)
        return EXIT_AGENT
    except AgentRunError as exc:
        log.error("agent run failed: %s", exc)
        return EXIT_AGENT
    for result in output.agent_results:
        log.info("agent %s model=%s elapsed=%.1fs", result.agent, result.model, result.elapsed_s)
    if args.no_post:
        log.info("no-post: brief generated but not published")
        return EXIT_OK

    try:
        note_path = note_writer(
            args.date,
            output.note_markdown,
            evidence_path=evidence_path,
            generated_by=output.generated_by,
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
        replaced = getattr(scrub_result, "replaced", 0)
        log.info("slack posted (scrub replacements: %s)", replaced)
    except SlackPostError as exc:
        log.error("slack post failed: %s", exc)
        return EXIT_PUBLISH
    return EXIT_OK


def _default_krx_client() -> object:
    from quantpilot.services.research_agents.collectors.naver_market import NaverMarketClient

    return NaverMarketClient()


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
