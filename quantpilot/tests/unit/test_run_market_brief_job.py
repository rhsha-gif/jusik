from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from quantpilot.jobs import run_market_brief as job
from quantpilot.services.research_agents.models import AgentResult
from quantpilot.services.research_agents.pipeline_market import MarketBriefOutput
from quantpilot.services.research_agents.publish.notes import NoteExistsError, write_market_note
from quantpilot.services.research_agents.publish.slack import SlackPostError
from quantpilot.services.research_agents.runner import AgentEmptyOutput
from quantpilot.tests.unit.test_research_collectors import _WATCHLIST, FakeKrxClient, FakeNewsClient


def _output() -> MarketBriefOutput:
    result = AgentResult(agent="qp-market-editor", model="opus", text="x", elapsed_s=1.0, exit_code=0)
    return MarketBriefOutput(slack_text="📈 시황", note_markdown="# 시황", agent_results=[result])


class Recorder:
    def __init__(self) -> None:
        self.posts: list[str] = []
        self.pipeline_calls = 0

    def pipeline(self, bundle: Any, **kwargs: Any) -> MarketBriefOutput:
        self.pipeline_calls += 1
        assert kwargs["evidence_path"].exists()
        return _output()

    def poster(self, text: str) -> Any:
        self.posts.append(text)

        class _Scrub:
            replaced = 0

        return _Scrub()


def _run(tmp_path: Path, argv: list[str], recorder: Recorder, **overrides: Any) -> int:
    kwargs: dict[str, Any] = dict(
        krx_client_factory=FakeKrxClient,
        news_client_factory=FakeNewsClient,
        pipeline=recorder.pipeline,
        poster=recorder.poster,
        note_writer=lambda *a, **k: write_market_note(*a, root=tmp_path / "ledger", **k),
        validate_env=lambda: None,
        watchlist_loader=lambda: list(_WATCHLIST),
    )
    kwargs.update(overrides)
    return job.run(["--date", "2026-09-03", "--out-dir", str(tmp_path / "out"), *argv], **kwargs)


def test_dry_run_writes_evidence_only(tmp_path: Path) -> None:
    recorder = Recorder()
    assert _run(tmp_path, ["--dry-run"], recorder) == job.EXIT_OK
    evidence = tmp_path / "out" / "evidence_2026-09-03.json"
    assert evidence.exists()
    assert json.loads(evidence.read_text(encoding="utf-8"))["signal_input"] is False
    assert recorder.pipeline_calls == 0 and recorder.posts == []
    assert (tmp_path / "out" / "run_2026-09-03.log").exists()


def test_no_post_runs_agents_but_publishes_nothing(tmp_path: Path) -> None:
    recorder = Recorder()
    assert _run(tmp_path, ["--no-post"], recorder) == job.EXIT_OK
    assert recorder.pipeline_calls == 1 and recorder.posts == []
    assert not (tmp_path / "ledger" / "market").exists()


def test_full_run_writes_note_then_posts(tmp_path: Path) -> None:
    recorder = Recorder()
    assert _run(tmp_path, [], recorder) == job.EXIT_OK
    note = tmp_path / "ledger" / "market" / "2026-09-03.md"
    assert note.exists() and "generated_by: qp-market-editor/opus" in note.read_text(encoding="utf-8")
    assert recorder.posts == ["📈 시황"]


def test_collection_failure_exits_2_without_evidence(tmp_path: Path) -> None:
    recorder = Recorder()
    code = _run(tmp_path, [], recorder, krx_client_factory=lambda: FakeKrxClient(drop_symbol="000660"))
    assert code == job.EXIT_COLLECTION
    assert not (tmp_path / "out" / "evidence_2026-09-03.json").exists()


def test_empty_agent_output_exits_3(tmp_path: Path) -> None:
    recorder = Recorder()

    def failing(bundle: Any, **kwargs: Any) -> MarketBriefOutput:
        raise AgentEmptyOutput("editor empty")

    assert _run(tmp_path, [], recorder, pipeline=failing) == job.EXIT_AGENT


def test_publish_failures_exit_4(tmp_path: Path) -> None:
    recorder = Recorder()

    def bad_poster(text: str) -> Any:
        raise SlackPostError("webhook unreachable")

    assert _run(tmp_path, [], recorder, poster=bad_poster) == job.EXIT_PUBLISH
    # the note was still written before the post failed
    assert (tmp_path / "ledger" / "market" / "2026-09-03.md").exists()
    # and a second run without --force refuses to overwrite it
    assert _run(tmp_path, [], Recorder()) == job.EXIT_PUBLISH
    assert _run(tmp_path, ["--force"], Recorder()) == job.EXIT_OK


def test_skip_news_and_no_slack(tmp_path: Path) -> None:
    recorder = Recorder()

    def no_news_client() -> Any:
        raise AssertionError("news client must not be built with --skip-news")

    assert _run(tmp_path, ["--skip-news", "--no-slack"], recorder, news_client_factory=no_news_client) == job.EXIT_OK
    evidence = json.loads((tmp_path / "out" / "evidence_2026-09-03.json").read_text(encoding="utf-8"))
    assert evidence["news"] == []
    assert (tmp_path / "ledger" / "market" / "2026-09-03.md").exists()
    assert recorder.posts == []


def test_closed_days_exit_0_without_collecting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setenv("KRX_HOLIDAYS", "2026-09-03")
    assert _run(tmp_path, [], recorder) == job.EXIT_OK
    assert not (tmp_path / "out" / "evidence_2026-09-03.json").exists()
    assert job.is_closed_day(date(2026, 9, 5), "") is True  # Saturday
    assert job.is_closed_day(date(2026, 9, 3), "") is False


def test_bad_date_is_rejected_before_any_file_is_named(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        job.run(["--date", "2026-13-99", "--out-dir", str(tmp_path / "out")], validate_env=lambda: None)
    assert not (tmp_path / "out").exists()


def test_note_exists_error_type_is_file_exists() -> None:
    assert issubclass(NoteExistsError, FileExistsError)
