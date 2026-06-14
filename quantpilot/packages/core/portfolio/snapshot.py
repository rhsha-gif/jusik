from __future__ import annotations

from datetime import datetime, timezone

from quantpilot.packages.core.schemas import PortfolioSnapshot, utc_now


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def mark_snapshot_staleness(
    snapshot: PortfolioSnapshot,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> PortfolioSnapshot:
    """Return a copy marked stale when its state timestamp is outside the allowed window."""

    if snapshot.is_stale:
        return snapshot
    current_time = _aware(now or utc_now())
    as_of = _aware(snapshot.as_of)
    age_seconds = (current_time - as_of).total_seconds()
    if age_seconds < 0:
        return snapshot.model_copy(
            update={
                "is_stale": True,
                "stale_reason": "portfolio_snapshot_as_of_in_future",
            }
        )
    if age_seconds > max_age_seconds:
        return snapshot.model_copy(
            update={
                "is_stale": True,
                "stale_reason": f"portfolio_snapshot_age_seconds={round(age_seconds, 3)} exceeds max_age_seconds={max_age_seconds}",
            }
        )
    return snapshot


def runtime_snapshot_block_reason(snapshot: PortfolioSnapshot | None) -> str | None:
    if snapshot is None:
        return "missing_portfolio_snapshot"
    if snapshot.is_fixture:
        return "fixture_portfolio_snapshot"
    if snapshot.is_stale:
        return snapshot.stale_reason or "stale_portfolio_snapshot"
    if not snapshot.source.strip():
        return "missing_portfolio_snapshot_source"
    return None
