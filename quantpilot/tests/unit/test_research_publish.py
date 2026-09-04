from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantpilot.services.research_agents.publish import slack
from quantpilot.services.research_agents.publish.notes import (
    NoteExistsError,
    read_recent_market_notes,
    write_candidate_note,
    write_market_note,
)
from quantpilot.services.research_agents.publish.scrub import REDACTED, scrub


def test_scrub_redacts_credential_env_values_and_known_shapes() -> None:
    env = {"NAVER_CLIENT_SECRET": "s3cr3tvalue99", "BROKER_MODE": "mock", "SHORT_KEY": "abc"}
    text = (
        "키는 s3cr3tvalue99 이고 mock 모드. abc 는 짧다.\n"
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123\n"
        "sk-abcdefghijklmnopqrstuvwxyz1234\n"
        "xoxb-0000000000-fakefakefake\n"
        "https://hooks.slack.com/services/T000/B000/xxxxxxxx\n"
        "hash 0123456789abcdef0123456789abcdef\n"
        "[봉인] 이 줄은 통째로 사라진다\n"
        "정상 줄\n"
    )
    result = scrub(text, env)

    assert "s3cr3tvalue99" not in result.text
    assert "mock" in result.text and "abc 는" in result.text  # short/benign values survive
    for leaked in ("Bearer abcdefghijklmnopqrstuvwxyz0123", "sk-abcdef", "xoxb-0000000000", "hooks.slack.com/services", "0123456789abcdef0123456789abcdef", "통째로"):
        assert leaked not in result.text
    assert result.text.count(REDACTED) >= 6
    assert result.replaced >= 6
    assert result.text.endswith("정상 줄\n")


def test_scrub_leaves_clean_text_untouched() -> None:
    result = scrub("코스피 3,015.0 (+1.17%) 외국인 +1,200억\n", {"BROKER_MODE": "mock"})
    assert result.replaced == 0
    assert result.text == "코스피 3,015.0 (+1.17%) 외국인 +1,200억\n"


def test_slack_post_scrubs_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, object] = {}

    def fake_send(url: str, body: bytes, headers: dict[str, str], timeout_s: float) -> tuple[int, str]:
        sent["url"] = url
        sent["body"] = json.loads(body.decode("utf-8"))
        return 200, "ok"

    monkeypatch.setenv("QUANTPILOT_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T1/B1/secretpath")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "leakedsecret123")
    result = slack.post_webhook("본문 leakedsecret123 끝", send=fake_send)

    assert sent["url"] == "https://hooks.slack.com/services/T1/B1/secretpath"
    assert "leakedsecret123" not in sent["body"]["text"]
    assert result.replaced == 1


def test_slack_post_requires_https_webhook_and_ok_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUANTPILOT_SLACK_WEBHOOK_URL", raising=False)
    with pytest.raises(slack.SlackPostError, match="not set"):
        slack.post_webhook("x", send=lambda *_: (200, "ok"))
    with pytest.raises(slack.SlackPostError, match="returned 500"):
        slack.post_webhook("x", url="https://hooks.slack.com/services/a/b/c", send=lambda *_: (500, "invalid_payload"))


def test_bot_token_path_posts_a_dm_and_never_leaks_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, object] = {}

    def fake_send(url: str, body: bytes, headers: dict[str, str], timeout_s: float) -> tuple[int, str]:
        sent["url"] = url
        sent["headers"] = headers
        sent["body"] = json.loads(body.decode("utf-8"))
        return 200, json.dumps({"ok": True, "ts": "1.2"})

    monkeypatch.delenv("QUANTPILOT_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("QUANTPILOT_SLACK_CHANNEL", raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-not-a-real-token-1234567890")
    monkeypatch.setenv("SLACK_ALLOWED_USER_ID", "U0000TEST")
    assert slack.delivery_path() == "bot"
    slack.post("본문 xoxb-not-a-real-token-1234567890 끝", send=fake_send)
    assert sent["url"] == slack.POST_MESSAGE_URL
    assert sent["body"]["channel"] == "U0000TEST"
    assert "xoxb-not-a-real-token" not in sent["body"]["text"]  # the token value is scrubbed from the text
    assert sent["headers"]["Authorization"].startswith("Bearer ")

    def failing(url: str, body: bytes, headers: dict[str, str], timeout_s: float) -> tuple[int, str]:
        return 200, json.dumps({"ok": False, "error": "channel_not_found"})

    with pytest.raises(slack.SlackPostError, match="channel_not_found"):
        slack.post("x", send=failing)
    monkeypatch.delenv("SLACK_BOT_TOKEN")
    assert slack.delivery_path() == ""
    with pytest.raises(slack.SlackPostError, match="no Slack delivery configured"):
        slack.post("x", send=fake_send)


def test_slack_module_has_no_unscrubbed_send_entry_point() -> None:
    public = [name for name in dir(slack) if not name.startswith("_") and callable(getattr(slack, name))]
    assert "post_webhook" in public
    assert not any(name.startswith("send") or name.startswith("post_raw") for name in public)
    assert {"post", "post_webhook", "post_bot_message"} <= set(public)


def test_market_note_has_frontmatter_and_refuses_overwrite(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence_2026-09-03.json"
    evidence.write_text("{}", encoding="utf-8")
    path = write_market_note("2026-09-03", "# 시황\n\n본문", evidence_path=evidence, generated_by="qp-market-editor/opus", root=tmp_path)

    assert path == tmp_path / "market" / "2026-09-03.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\ntype: market-brief\ndate: 2026-09-03\n")
    assert "signal_input: false" in text and "generated_by: qp-market-editor/opus" in text
    assert text.endswith("본문\n")
    with pytest.raises(NoteExistsError):
        write_market_note("2026-09-03", "다시", evidence_path=evidence, generated_by="x", root=tmp_path)
    write_market_note("2026-09-03", "다시", evidence_path=evidence, generated_by="x", root=tmp_path, force=True)
    assert "다시" in path.read_text(encoding="utf-8")


def test_candidate_note_is_proposed_and_recent_notes_are_newest_first(tmp_path: Path) -> None:
    evidence = tmp_path / "e.json"
    evidence.write_text("{}", encoding="utf-8")
    path = write_candidate_note("2026-09-03", "005930", "## 사실\n...", evidence_path=evidence, generated_by="x", root=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert path.name == "2026-09-03-005930.md"
    assert "status: proposed" in text and "candidate_id: 2026-09-03-005930" in text and "symbol: 005930" in text

    for day in ("2026-09-01", "2026-09-03", "2026-09-02"):
        write_market_note(day, f"# {day}", evidence_path=evidence, generated_by="x", root=tmp_path, force=True)
    recent = read_recent_market_notes(2, root=tmp_path)
    assert [d for d, _ in recent] == ["2026-09-03", "2026-09-02"]


def test_ledger_root_defaults_to_env_or_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from quantpilot.services.research_agents.publish.notes import ledger_root

    monkeypatch.setenv("QUANTPILOT_LEDGER_ROOT", str(tmp_path))
    assert ledger_root() == tmp_path
    monkeypatch.delenv("QUANTPILOT_LEDGER_ROOT")
    assert ledger_root().name == "investment-decisions"
