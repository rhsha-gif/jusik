from __future__ import annotations

from pathlib import Path

from quantpilot.services.briefing import daily_briefing

_BRIEFING_DIR = Path(__file__).resolve().parents[2] / "services" / "briefing"
_FORBIDDEN_IMPORTS = (
    "core.signals",
    "core.portfolio",
    "core.execution",
    "packages.brokers",
    "harness_service",
)


def test_daily_briefing_is_deterministic_and_read_only() -> None:
    first = daily_briefing()
    second = daily_briefing()

    assert [card.card_id for card in first] == [card.card_id for card in second]
    assert first, "fixture briefing must not be empty"
    published = [card.published_at for card in first]
    assert published == sorted(published, reverse=True)
    for card in first:
        assert card.signal_input is False
        assert card.summary and card.headline


def test_briefing_boundary_never_imports_trading_code() -> None:
    for source_file in _BRIEFING_DIR.glob("*.py"):
        text = source_file.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_IMPORTS:
            assert forbidden not in text, (
                f"{source_file.name} references '{forbidden}' — the briefing "
                "boundary must stay isolated from trading code (design §4.7)"
            )
