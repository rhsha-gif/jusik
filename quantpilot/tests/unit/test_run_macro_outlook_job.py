from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from quantpilot.jobs import run_macro_outlook as job
from quantpilot.services.research_agents.models import AgentResult, MacroEvidence, MacroEvidenceBundle
from quantpilot.services.research_agents.pipeline_macro import MacroOutlookOutput
from quantpilot.services.research_agents.publish.notes import write_research_note


def _macro(**kwargs: Any) -> MacroEvidence:
    return MacroEvidence(as_of="2026-09-13", collected_at="2026-09-13T20:00:00+09:00", **kwargs)


def _output() -> MacroOutlookOutput:
    return MacroOutlookOutput(
        slack_text="🧭 매크로 전망 2026-09-13",
        note_markdown="# 매크로 전망 2026-09-13\n## 출처\n- 증거",
        scenarios={"base_case": "x", "scenarios": []},
        agent_results=[AgentResult(agent="qp-strat-editor", model="opus", text="t", elapsed_s=1.0, exit_code=0)],
    )


def test_dry_run_writes_evidence_with_skipped_macro_and_no_agents(tmp_path: Path) -> None:
    calls: list[str] = []
    code = job.run(
        ["--date", "2026-09-13", "--out-dir", str(tmp_path), "--dry-run", "--skip-macro", "--skip-news"],
        pipeline=lambda *a, **k: calls.append("pipeline") or _output(),
        validate_env=lambda: None,
        open_decisions=lambda: [("2026-09-10-usd-etf-preconversion", "---\nstatus: open\n---")],
        environ={},
    )
    assert code == job.EXIT_OK and calls == []
    bundle = MacroEvidenceBundle.model_validate_json((tmp_path / "evidence_macro_2026-09-13.json").read_text(encoding="utf-8"))
    assert bundle.macro.skipped == ["macro collection skipped (--skip-macro)"] and bundle.news == []
    assert bundle.open_decisions == ["2026-09-10-usd-etf-preconversion"] and bundle.signal_input is False


def test_full_run_collects_writes_note_then_posts(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    posted: list[str] = []
    collected: list[dict[str, Any]] = []

    def collector(**kwargs: Any) -> MacroEvidence:
        collected.append(kwargs)
        assert kwargs["environ"] == {"ECOS_API_KEY": "k"} and kwargs["as_of"] == date(2026, 9, 13)
        return _macro(regime={"quadrant": "Q2_growth_up_inflation_up", "method": "m"})

    code = job.run(
        ["--date", "2026-09-13", "--out-dir", str(tmp_path / "out"), "--skip-news"],
        macro_collector=collector,
        pipeline=lambda bundle, **k: _output(),
        poster=lambda text: posted.append(text),
        note_writer=lambda *a, **k: write_research_note(*a, root=ledger, **k),
        validate_env=lambda: None,
        open_decisions=lambda: [],
        environ={"ECOS_API_KEY": "k"},
    )
    assert code == job.EXIT_OK and len(collected) == 1 and posted == ["🧭 매크로 전망 2026-09-13"]
    note = ledger / "research" / "2026-09-13-macro-outlook.md"
    text = note.read_text(encoding="utf-8")
    assert "type: macro-outlook" in text and "signal_input: false" in text and "regime: Q2_growth_up_inflation_up" in text
    assert (tmp_path / "out" / "scenarios_2026-09-13.json").exists()
    assert "k" not in json.dumps(json.loads((tmp_path / "out" / "evidence_macro_2026-09-13.json").read_text(encoding="utf-8"))["macro"]["skipped"])


def test_existing_note_without_force_is_a_publish_failure(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    write_research_note("2026-09-13", "macro-outlook", "old", note_type="macro-outlook", evidence_path=tmp_path / "e.json", generated_by="x", root=ledger)
    code = job.run(
        ["--date", "2026-09-13", "--out-dir", str(tmp_path / "out"), "--skip-news", "--skip-macro", "--no-slack"],
        pipeline=lambda bundle, **k: _output(),
        note_writer=lambda *a, **k: write_research_note(*a, root=ledger, **k),
        validate_env=lambda: None,
        open_decisions=lambda: [],
        environ={},
    )
    assert code == job.EXIT_PUBLISH


def test_agent_failure_is_exit_3_and_writes_nothing(tmp_path: Path) -> None:
    from quantpilot.services.research_agents.runner import AgentEmptyOutput

    def failing(bundle: Any, **k: Any) -> MacroOutlookOutput:
        raise AgentEmptyOutput("qp-strat-scenario-writer: fewer than two scenarios returned")

    written: list[Any] = []
    code = job.run(
        ["--date", "2026-09-13", "--out-dir", str(tmp_path), "--skip-news", "--skip-macro"],
        pipeline=failing,
        note_writer=lambda *a, **k: written.append(a),
        validate_env=lambda: None,
        open_decisions=lambda: [],
        environ={},
    )
    assert code == job.EXIT_AGENT and written == []


def test_research_note_slug_is_validated(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        write_research_note("2026-09-13", "bad slug/with", "x", note_type="t", evidence_path=tmp_path / "e", generated_by="g", root=tmp_path)
