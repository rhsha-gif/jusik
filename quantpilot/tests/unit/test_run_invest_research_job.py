from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quantpilot.jobs import run_invest_research as job
from quantpilot.services.research_agents.models import AgentResult
from quantpilot.services.research_agents.pipeline_invest import Candidate, CandidateResearch, InvestResearchOutput
from quantpilot.tests.unit.test_research_collectors import _WATCHLIST, FakeKrxClient, FakeNewsClient


class Recorder:
    def __init__(self) -> None:
        self.posts: list[str] = []
        self.kwargs: dict[str, Any] = {}

    def pipeline(self, **kwargs: Any) -> InvestResearchOutput:
        self.kwargs = kwargs
        symbol = kwargs["symbol"] or "000660"
        agent = AgentResult(agent="qp-invest-stock-researcher", model=kwargs["model"], text="x", elapsed_s=1.0, exit_code=0)
        research = CandidateResearch(
            candidate=Candidate(symbol=symbol, name=kwargs["symbol_validator"](symbol) or "?"),
            base_rates=[],
            researcher=agent,
            refuter=agent,
            note_markdown=f"# 후보 {symbol}\n\n## 사실\n...",
        )
        return InvestResearchOutput(candidates=[research], direction=agent, agent_results=[agent])

    def poster(self, text: str) -> None:
        self.posts.append(text)


def _run(tmp_path: Path, argv: list[str], recorder: Recorder, **overrides: Any) -> int:
    kwargs: dict[str, Any] = dict(
        krx_client_factory=FakeKrxClient,
        news_client_factory=FakeNewsClient,
        pipeline=recorder.pipeline,
        poster=recorder.poster,
        validate_env=lambda: None,
        watchlist_loader=lambda: list(_WATCHLIST),
        ledger=tmp_path / "ledger",
    )
    kwargs.update(overrides)
    return job.run(["--date", "2026-09-03", "--out-dir", str(tmp_path / "out"), *argv], **kwargs)


def test_symbol_run_writes_a_proposed_candidate_note_and_posts_one_line(tmp_path: Path) -> None:
    recorder = Recorder()
    assert _run(tmp_path, ["--symbol", "005930"], recorder) == job.EXIT_OK
    note = tmp_path / "ledger" / "candidates" / "2026-09-03-005930.md"
    text = note.read_text(encoding="utf-8")
    assert "status: proposed" in text and "candidate_id: 2026-09-03-005930" in text
    assert recorder.kwargs["symbol"] == "005930" and recorder.kwargs["judge_model"] == "fable"
    assert (tmp_path / "out" / "evidence_2026-09-03.json").exists()
    assert len(recorder.posts) == 1 and "후보 1건" in recorder.posts[0] and str(note) in recorder.posts[0]


def test_theme_run_reuses_existing_evidence(tmp_path: Path) -> None:
    recorder = Recorder()
    assert _run(tmp_path, ["--symbol", "005930", "--dry-run"], recorder) == job.EXIT_OK
    evidence = tmp_path / "out" / "evidence_2026-09-03.json"
    stamp = evidence.stat().st_mtime_ns
    base = tmp_path / "out" / "base_rate_2026-09-03_005930.json"
    assert base.exists() and json.loads(base.read_text(encoding="utf-8"))[0]["condition"] == "insufficient history"

    assert _run(tmp_path, ["--theme", "AI 메모리"], recorder) == job.EXIT_OK
    assert evidence.stat().st_mtime_ns == stamp  # reused, not rewritten
    assert recorder.kwargs["theme"] == "AI 메모리" and recorder.kwargs["symbol"] is None
    assert (tmp_path / "ledger" / "candidates" / "2026-09-03-000660.md").exists()


def test_no_post_and_duplicate_note(tmp_path: Path) -> None:
    recorder = Recorder()
    assert _run(tmp_path, ["--symbol", "005930", "--no-post"], recorder) == job.EXIT_OK
    assert not (tmp_path / "ledger" / "candidates").exists()
    assert _run(tmp_path, ["--symbol", "005930"], recorder) == job.EXIT_OK
    assert _run(tmp_path, ["--symbol", "005930"], recorder) == job.EXIT_PUBLISH
    assert _run(tmp_path, ["--symbol", "005930", "--force"], recorder) == job.EXIT_OK


def test_skip_news_and_no_slack(tmp_path: Path) -> None:
    recorder = Recorder()

    def no_news_client() -> Any:
        raise AssertionError("news client must not be built with --skip-news")

    assert _run(tmp_path, ["--symbol", "005930", "--skip-news", "--no-slack"], recorder, news_client_factory=no_news_client) == job.EXIT_OK
    assert (tmp_path / "ledger" / "candidates" / "2026-09-03-005930.md").exists()
    assert recorder.posts == []


def test_collection_failure_exits_2(tmp_path: Path) -> None:
    code = _run(tmp_path, ["--symbol", "005930"], Recorder(), krx_client_factory=lambda: FakeKrxClient(drop_symbol="000660"))
    assert code == job.EXIT_COLLECTION
