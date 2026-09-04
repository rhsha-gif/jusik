"""Investment research: theme or symbol → candidate notes (`status: proposed`) in the ledger.

Reuses today's evidence file when the market brief already produced one, else
collects. Exit codes match `run_market_brief`: 0 ok, 2 collection failed, 3 an
agent produced nothing, 4 publishing failed. Slack receives one line only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence

from quantpilot.services.api.dependencies import validate_generic_runtime_environment
from quantpilot.services.research_agents.collectors.evidence import build_evidence, evidence_path, read_evidence, write_evidence
from quantpilot.services.research_agents.collectors.krx import CollectionError, collect_market_snapshot, load_watchlist
from quantpilot.services.research_agents.collectors.naver_news import DEFAULT_QUERIES, NewsCollectionError, collect_news
from quantpilot.services.research_agents.pipeline_invest import InvestResearchOutput, compute_base_rates, run_invest_pipeline
from quantpilot.services.research_agents.publish.notes import NoteExistsError, ledger_root, write_candidate_note
from quantpilot.services.research_agents.publish.slack import SlackPostError, post
from quantpilot.services.research_agents.runner import AgentEmptyOutput, AgentRunError

EXIT_OK = 0
EXIT_COLLECTION = 2
EXIT_AGENT = 3
EXIT_PUBLISH = 4
DEFAULT_OUT_DIR = Path(".research_agents_out")
DEFAULT_MODEL = os.environ.get("QUANTPILOT_RESEARCH_MODEL", "opus")
DEFAULT_JUDGE_MODEL = os.environ.get("QUANTPILOT_RESEARCH_JUDGE_MODEL", "fable")
_HISTORY_YEARS = 5
_REPO_ROOT = Path(__file__).resolve().parents[2]

log = logging.getLogger("quantpilot.invest_research")


def _iso_date(value: str) -> str:
    """argparse type: a canonical YYYY-MM-DD string or an error (file names derive from it)."""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--date must be YYYY-MM-DD, got {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Candidate research for the private investment ledger (proposals only).")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--theme", help="theme sentence for the scout")
    target.add_argument("--symbol", help="6-digit KRX code; skips the scout")
    parser.add_argument("--date", type=_iso_date, default=date.today().isoformat())
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="evidence (and base rates for --symbol) only; no agents")
    parser.add_argument("--no-post", action="store_true", help="run the agents but write nothing to the ledger or Slack")
    parser.add_argument("--force", action="store_true", help="overwrite an existing candidate note")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--skip-news", action="store_true", help="collect without Naver news when no evidence file exists yet")
    parser.add_argument("--no-slack", action="store_true", help="write candidate notes but do not post to Slack")
    return parser


def _setup_logging(out_dir: Path, session_date: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(out_dir / f"invest_{session_date}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log.handlers.clear()
    log.addHandler(handler)
    log.addHandler(stream)
    log.setLevel(logging.INFO)


def _history_via(client: Any, session_date: str) -> Callable[[str], Sequence[dict[str, Any]]]:
    end = session_date.replace("-", "")
    start = (date.fromisoformat(session_date) - timedelta(days=365 * _HISTORY_YEARS)).isoformat().replace("-", "")

    def _history(symbol: str) -> Sequence[dict[str, Any]]:
        return client.stock_ohlcv(symbol, start, end)

    return _history


def _validator_via(client: Any, watchlist: list[Any]) -> Callable[[str], str | None]:
    known = {entry.code: entry.name for entry in watchlist}

    def _validate(symbol: str) -> str | None:
        if symbol in known:
            return known[symbol]
        if not (symbol.isdigit() and len(symbol) == 6):
            return None
        try:
            name = client.ticker_name(symbol)
        except Exception:  # noqa: BLE001 - unknown symbol is simply not a candidate
            return None
        return name or None

    return _validate


def run(
    argv: Sequence[str] | None = None,
    *,
    krx_client_factory: Callable[[], Any] | None = None,
    news_client_factory: Callable[[], Any] | None = None,
    pipeline: Callable[..., InvestResearchOutput] = run_invest_pipeline,
    poster: Callable[..., object] = post,
    note_writer: Callable[..., Path] = write_candidate_note,
    validate_env: Callable[[], object] = validate_generic_runtime_environment,
    watchlist_loader: Callable[[], list] = load_watchlist,
    ledger: Path | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    validate_env()
    out_dir = Path(args.out_dir)
    _setup_logging(out_dir, args.date)
    root = ledger or ledger_root()

    try:
        krx_client = (krx_client_factory or _default_krx_client)()
        watchlist = watchlist_loader()
        existing = evidence_path(out_dir, args.date)
        if existing.exists():
            bundle = read_evidence(existing)
            log.info("evidence reused: %s", existing)
        else:
            snapshot = collect_market_snapshot(args.date, krx_client, watchlist)
            if args.skip_news:
                news = []
                log.warning("news skipped (--skip-news)")
            else:
                news_client = (news_client_factory or _default_news_client)()
                news = collect_news(news_client, [e.name for e in watchlist] + list(DEFAULT_QUERIES))
            bundle = build_evidence(args.date, snapshot, news)
            write_evidence(bundle, out_dir)
            log.info("evidence written: %s", existing)
    except (CollectionError, NewsCollectionError) as exc:
        log.error("collection failed: %s", exc)
        return EXIT_COLLECTION

    history = _history_via(krx_client, args.date)
    validator = _validator_via(krx_client, watchlist)
    if args.dry_run:
        if args.symbol:
            try:
                rates = compute_base_rates(history(args.symbol))
            except Exception as exc:  # noqa: BLE001 - history fetch is a collection step
                log.error("history failed: %s", exc)
                return EXIT_COLLECTION
            target = out_dir / f"base_rate_{args.date}_{args.symbol}.json"
            target.write_text(json.dumps(rates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            log.info("base rates written: %s", target)
        return EXIT_OK

    try:
        output = pipeline(
            theme=args.theme,
            symbol=args.symbol,
            bundle=bundle,
            evidence_path=existing.resolve(),
            ledger_root=root,
            cwd=_REPO_ROOT,
            model=args.model,
            judge_model=args.judge_model,
            history=history,
            symbol_validator=validator,
            watchlist=[{"code": e.code, "name": e.name, "theme": e.theme} for e in watchlist],
            max_candidates=args.max_candidates,
        )
    except (AgentEmptyOutput, AgentRunError, ValueError, CollectionError) as exc:
        log.error("research failed: %s", exc)
        return EXIT_AGENT
    for result in output.agent_results:
        log.info("agent %s model=%s elapsed=%.1fs", result.agent, result.model, result.elapsed_s)
    if args.no_post:
        log.info("no-post: %d candidate note(s) generated but not written", len(output.candidates))
        return EXIT_OK

    written: list[Path] = []
    try:
        for research in output.candidates:
            path = note_writer(
                args.date,
                research.candidate.symbol,
                research.note_markdown,
                evidence_path=existing.resolve(),
                generated_by=output.generated_by,
                root=root,
                force=args.force,
            )
            written.append(path)
            log.info("candidate note written: %s", path)
    except NoteExistsError as exc:
        log.error("%s (use --force to overwrite)", exc)
        return EXIT_PUBLISH
    if args.no_slack:
        log.info("no-slack: %d note(s) written, Slack skipped", len(written))
        return EXIT_OK
    try:
        poster(f"🔎 후보 {len(written)}건 제안됨 ({args.date}):\n" + "\n".join(f"- {p}" for p in written))
    except SlackPostError as exc:
        log.error("slack post failed: %s", exc)
        return EXIT_PUBLISH
    return EXIT_OK


def _default_krx_client() -> Any:
    from quantpilot.services.research_agents.collectors.naver_market import NaverMarketClient

    return NaverMarketClient()


def _default_news_client() -> Any:
    from quantpilot.services.research_agents.collectors.naver_news import NaverNewsClient

    return NaverNewsClient()


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
