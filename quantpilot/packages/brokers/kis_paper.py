from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Protocol

from quantpilot.packages.core.kis_paper import KisBalanceResult, KisPaperClient
from quantpilot.packages.core.marketdata.kis_paper import KisPaperMarketDataProvider
from quantpilot.packages.core.schemas import (
    BrokerMode,
    BrokerOrder,
    Fill,
    OrderPlan,
    PortfolioPosition,
    PortfolioSnapshot,
    utc_now,
)


class PaperOrderSubmissionGateway(Protocol):
    """The only authority allowed to cross the external paper order boundary."""

    def submit_prepared_order(
        self,
        order_plan: OrderPlan,
    ) -> tuple[BrokerOrder, list[Fill]]: ...


@dataclass(frozen=True)
class PaperPortfolioLossMetrics:
    daily_loss_ratio: float
    monthly_loss_ratio: float
    as_of: datetime

    def __post_init__(self) -> None:
        for value in (self.daily_loss_ratio, self.monthly_loss_ratio):
            if not isfinite(value) or value < -1:
                raise ValueError("paper portfolio loss ratios must be finite and at least -1")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("paper portfolio loss metrics require an aware timestamp")


class PaperPortfolioLossProvider(Protocol):
    def get_loss_metrics(
        self,
        balance: KisBalanceResult,
        *,
        observed_at: datetime,
    ) -> PaperPortfolioLossMetrics: ...


class PaperSectorProvider(Protocol):
    def sector_for_symbol(self, symbol: str) -> str | None: ...


class SecurityMetadataSectorProvider:
    """Immutable sector lookup built from the operator security universe."""

    def __init__(self, securities: Sequence[Mapping[str, object]]) -> None:
        sectors: dict[str, str] = {}
        for security in securities:
            symbol = str(
                security.get("ticker") or security.get("symbol") or ""
            ).strip().upper()
            sector = str(security.get("sector") or "").strip()
            if not symbol:
                raise ValueError("paper security metadata requires a symbol")
            if not sector or sector.lower() == "unknown":
                raise ValueError(
                    "paper security metadata requires an authoritative sector"
                )
            previous = sectors.get(symbol)
            if previous is not None and previous != sector:
                raise ValueError("paper security metadata contains conflicting sectors")
            sectors[symbol] = sector
        if not sectors:
            raise ValueError("paper security metadata cannot be empty")
        self._sectors = sectors

    def sector_for_symbol(self, symbol: str) -> str | None:
        return self._sectors.get(symbol.strip().upper())


class KisPaperBrokerAdapter:
    """KIS paper portfolio adapter with no direct order-submission capability.

    Reads use the strict KIS paper client.  Writes must pass through a durable
    submission gateway that owns the prepared/claimed/outcome journal.  Loss
    ratios are also mandatory because silently defaulting them to zero would
    disable the account-level loss gates.
    """

    mode = BrokerMode.paper.value
    environment = "kis_paper"

    def __init__(
        self,
        client: KisPaperClient,
        *,
        submission_gateway: PaperOrderSubmissionGateway,
        loss_provider: PaperPortfolioLossProvider,
        sector_provider: PaperSectorProvider,
        market_data_provider: KisPaperMarketDataProvider,
        clock: Callable[[], datetime] = utc_now,
        loss_max_age_seconds: int = 60,
    ) -> None:
        if isinstance(loss_max_age_seconds, bool) or loss_max_age_seconds <= 0:
            raise ValueError("paper loss-metric max age must be positive")
        self._client = client
        self._submission_gateway = submission_gateway
        self._loss_provider = loss_provider
        self._sector_provider = sector_provider
        self._clock = clock
        self._loss_max_age_seconds = loss_max_age_seconds
        self._market_data = market_data_provider

    @property
    def account_scope_fingerprint(self) -> str:
        return self._client.account_scope_fingerprint

    @property
    def submission_gateway(self) -> PaperOrderSubmissionGateway:
        """Expose the immutable write boundary for composition-time validation."""

        return self._submission_gateway

    @property
    def market_data_provider(self) -> KisPaperMarketDataProvider:
        return self._market_data

    def get_account(self, user_id: str) -> dict[str, str]:
        normalized_user = _required_user_id(user_id)
        return {
            "user_id": normalized_user,
            "account_scope_fingerprint": self.account_scope_fingerprint,
            "broker_mode": self.mode,
            "broker_environment": self.environment,
        }

    def get_cash(self, user_id: str) -> float:
        return self.get_positions(user_id).cash

    def get_positions(self, user_id: str) -> PortfolioSnapshot:
        normalized_user = _required_user_id(user_id)
        observed_at = self._observed_at()
        balance = self._client.get_balance(exchange="KRX")
        metrics = self._loss_provider.get_loss_metrics(
            balance,
            observed_at=observed_at,
        )
        self._require_fresh_loss_metrics(metrics, observed_at=observed_at)

        positions: list[PortfolioPosition] = []
        for item in balance.positions:
            if item.holding_quantity == 0:
                continue
            sector = self._sector_provider.sector_for_symbol(item.symbol)
            if sector is None or not sector.strip() or sector.strip().lower() == "unknown":
                raise RuntimeError(
                    "authoritative sector metadata is required for KIS paper positions"
                )
            positions.append(
                PortfolioPosition(
                    symbol=item.symbol,
                    quantity=float(item.holding_quantity),
                    orderable_quantity=float(item.orderable_quantity),
                    market_price=_positive_float(item.current_price, "position price"),
                    sector=sector.strip(),
                )
            )
        cash = _non_negative_float(balance.summary.deposit_amount, "cash")
        equity = _positive_float(balance.summary.net_asset_amount, "net assets")
        return PortfolioSnapshot(
            user_id=normalized_user,
            cash=cash,
            equity=equity,
            positions=positions,
            daily_loss_ratio=metrics.daily_loss_ratio,
            monthly_loss_ratio=metrics.monthly_loss_ratio,
            captured_at=observed_at,
            source="kis_paper_balance_reconciled",
        )

    def get_quote(self, symbol: str) -> float:
        snapshot = self._market_data.get_quotes([symbol])
        normalized = symbol.strip().upper()
        if not snapshot.data_quality.usable or set(snapshot.quotes) != {normalized}:
            raise RuntimeError("KIS paper quote is unavailable or unsafe")
        return snapshot.quotes[normalized].last

    def submit_order(self, order_plan: OrderPlan) -> tuple[BrokerOrder, list[Fill]]:
        """Delegate to the process-kill-safe journal; never call KIS directly."""

        broker_order, fills = self._submission_gateway.submit_prepared_order(order_plan)
        if (
            broker_order.order_plan_id != order_plan.order_plan_id
            or broker_order.broker_mode != BrokerMode.paper
        ):
            raise RuntimeError("durable paper gateway returned mismatched order evidence")
        if any(
            fill.order_plan_id != order_plan.order_plan_id
            or fill.broker_order_id != broker_order.broker_order_id
            for fill in fills
        ):
            raise RuntimeError("durable paper gateway returned mismatched fill evidence")
        return broker_order, fills

    def _observed_at(self) -> datetime:
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise RuntimeError("KIS paper broker clock must include a UTC offset")
        return observed_at

    def _require_fresh_loss_metrics(
        self,
        metrics: PaperPortfolioLossMetrics,
        *,
        observed_at: datetime,
    ) -> None:
        age = (observed_at - metrics.as_of).total_seconds()
        if age < 0 or age > self._loss_max_age_seconds:
            raise RuntimeError("paper portfolio loss metrics are not fresh")


def _required_user_id(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("paper broker user ID must not be blank")
    return normalized


def _positive_float(value: Decimal, label: str) -> float:
    parsed = float(value)
    if not isfinite(parsed) or parsed <= 0:
        raise ValueError(f"KIS paper {label} must be finite and positive")
    return parsed


def _non_negative_float(value: Decimal, label: str) -> float:
    parsed = float(value)
    if not isfinite(parsed) or parsed < 0:
        raise ValueError(f"KIS paper {label} must be finite and non-negative")
    return parsed
