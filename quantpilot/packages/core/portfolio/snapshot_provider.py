from __future__ import annotations

from typing import Protocol

from quantpilot.packages.core.schemas import PortfolioPosition, PortfolioSnapshot, utc_now


class PortfolioSnapshotProvider(Protocol):
    def get_snapshot(self, user_id: str) -> PortfolioSnapshot: ...


class StaticPortfolioSnapshotProvider:
    def __init__(
        self,
        *,
        source: str,
        cash: float,
        equity: float,
        positions: list[PortfolioPosition],
        daily_loss_ratio: float = 0.0,
        monthly_loss_ratio: float = 0.0,
    ) -> None:
        self.source = source
        self.cash = cash
        self.equity = equity
        self.positions = positions
        self.daily_loss_ratio = daily_loss_ratio
        self.monthly_loss_ratio = monthly_loss_ratio

    def get_snapshot(self, user_id: str) -> PortfolioSnapshot:
        generated_at = utc_now()
        return PortfolioSnapshot(
            user_id=user_id,
            cash=self.cash,
            equity=self.equity,
            positions=[position.model_copy() for position in self.positions],
            daily_loss_ratio=self.daily_loss_ratio,
            monthly_loss_ratio=self.monthly_loss_ratio,
            captured_at=generated_at,
            source=self.source,
            as_of=generated_at,
            generated_at=generated_at,
            is_fixture=False,
            is_stale=False,
        )


def default_runtime_snapshot_provider(*, source: str) -> StaticPortfolioSnapshotProvider:
    return StaticPortfolioSnapshotProvider(
        source=source,
        cash=6_000_000,
        equity=10_000_000,
        positions=[
            PortfolioPosition(symbol="CCC", quantity=10_000, market_price=100, sector="tech"),
            PortfolioPosition(symbol="DDD", quantity=20_000, market_price=100, sector="tech"),
            PortfolioPosition(symbol="EEE", quantity=10_000, market_price=100, sector="industrial"),
        ],
    )
