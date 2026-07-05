"""Fixture-first daily briefing cards (design doc §4.7).

Read-only: cards are curated content for the user, never a signal input.
The fixture set is deterministic (no network, no clock) so tests and the
DATA_MODE=fixture harness behave identically. A future stage may add real
collectors behind the same shape; promotion of briefing data into strategy
inputs requires a separate look-ahead-safe protocol first.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class BriefingCard(BaseModel):
    card_id: str
    source: str
    published_at: datetime
    headline: str
    summary: str
    related_symbols: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    signal_input: bool = False  # constant by design: briefing never feeds signals


_FIXTURE_CARDS: tuple[BriefingCard, ...] = (
    BriefingCard(
        card_id="brf_fixture_semis",
        source="fixture_wire",
        published_at=datetime(2026, 7, 3, 22, 30, tzinfo=timezone.utc),
        headline="Memory makers guide above consensus on AI capex",
        summary=(
            "Fixture summary: both large Korean memory names raised quarterly "
            "guidance citing sustained AI datacenter demand; analysts flag "
            "valuation dispersion within the supply chain."
        ),
        related_symbols=["005930", "000660"],
        tags=["semiconductor", "guidance"],
    ),
    BriefingCard(
        card_id="brf_fixture_battery",
        source="fixture_wire",
        published_at=datetime(2026, 7, 3, 6, 10, tzinfo=timezone.utc),
        headline="Battery cell prices stabilize after two-quarter slide",
        summary=(
            "Fixture summary: spot cell pricing flattened month-over-month; "
            "sell-side notes disagree on whether restocking has begun."
        ),
        related_symbols=["373220", "006400", "247540"],
        tags=["battery", "pricing"],
    ),
    BriefingCard(
        card_id="brf_fixture_macro",
        source="fixture_wire",
        published_at=datetime(2026, 7, 2, 23, 0, tzinfo=timezone.utc),
        headline="BOK holds rates; statement drops easing-bias sentence",
        summary=(
            "Fixture summary: policy rate unchanged; the statement removed "
            "prior language about room for additional easing, read as neutral-"
            "to-hawkish for financials."
        ),
        related_symbols=["105560", "055550"],
        tags=["macro", "rates"],
    ),
)


def daily_briefing() -> list[BriefingCard]:
    """Return the day's curated cards, newest first."""
    return sorted(_FIXTURE_CARDS, key=lambda card: card.published_at, reverse=True)
