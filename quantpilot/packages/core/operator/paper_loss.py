"""Fail-closed portfolio-loss metrics for the KIS paper operator."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Protocol

from quantpilot.packages.brokers.kis_paper import PaperPortfolioLossMetrics
from quantpilot.packages.core.kis_paper import (
    KIS_BALANCE_TR_ID,
    KisBalanceResult,
)
from quantpilot.packages.core.marketdata.kis_paper import (
    PaperTradingSessionAuthority,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperPortfolioLossBaseline,
    StateStoreProvenance,
)
from quantpilot.packages.core.schemas import utc_now


class PaperPortfolioLossStore(Protocol):
    """Read-only durable state needed to calculate paper loss metrics."""

    @property
    def provenance(self) -> StateStoreProvenance: ...

    def load_paper_portfolio_loss_baseline(
        self,
        business_date: date,
    ) -> PaperPortfolioLossBaseline | None: ...


class PaperPortfolioLossUnavailable(RuntimeError):
    """Safe reason code for loss evidence that cannot authorize trading."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class PersistentPaperPortfolioLossProvider:
    """Calculate loss ratios from a durable baseline and a fresh KIS balance.

    This provider never creates or rolls a baseline.  An operator must first
    persist explicitly sourced evidence through ``PaperStateStore``.
    """

    def __init__(
        self,
        store: PaperPortfolioLossStore,
        *,
        session_authority: PaperTradingSessionAuthority,
        clock: Callable[[], datetime] = utc_now,
        balance_max_age_seconds: int = 60,
        future_tolerance_seconds: int = 1,
    ) -> None:
        if (
            isinstance(balance_max_age_seconds, bool)
            or balance_max_age_seconds <= 0
        ):
            raise ValueError("paper balance max age must be positive")
        if (
            isinstance(future_tolerance_seconds, bool)
            or future_tolerance_seconds < 0
        ):
            raise ValueError("paper balance future tolerance cannot be negative")
        self._store = store
        self._session_authority = session_authority
        self._clock = clock
        self._balance_max_age_seconds = balance_max_age_seconds
        self._future_tolerance_seconds = future_tolerance_seconds

        provenance = store.provenance
        self._require_paper_provenance(provenance)
        self._store_id = provenance.store_id
        self._account_scope_fingerprint = provenance.account_scope_fingerprint

    @property
    def account_scope_fingerprint(self) -> str:
        """Opaque account binding used to verify operator wiring."""

        if self._account_scope_fingerprint is None:  # pragma: no cover
            raise PaperPortfolioLossUnavailable(
                "paper_loss_store_provenance_mismatch"
            )
        return self._account_scope_fingerprint

    def get_loss_metrics(
        self,
        balance: KisBalanceResult,
        *,
        observed_at: datetime,
    ) -> PaperPortfolioLossMetrics:
        received_at = self._aware_clock()
        self._require_fresh_observation(
            observed_at=observed_at,
            received_at=received_at,
        )
        business_date = self._require_consistent_session(
            observed_at=observed_at,
            received_at=received_at,
        )
        provenance = self._store.provenance
        self._require_bound_provenance(provenance)

        baseline = self._store.load_paper_portfolio_loss_baseline(business_date)
        if baseline is None:
            raise PaperPortfolioLossUnavailable(
                "paper_loss_baseline_missing"
            )
        self._require_baseline(
            baseline,
            provenance=provenance,
            business_date=business_date,
            observed_at=observed_at,
        )
        net_assets = self._net_assets(balance)
        daily_ratio = self._loss_ratio(
            net_assets,
            baseline.prior_close_equity,
        )
        monthly_ratio = self._loss_ratio(
            net_assets,
            baseline.month_start_equity,
        )
        return PaperPortfolioLossMetrics(
            daily_loss_ratio=daily_ratio,
            monthly_loss_ratio=monthly_ratio,
            as_of=observed_at,
        )

    @staticmethod
    def _require_paper_provenance(provenance: StateStoreProvenance) -> None:
        if (
            provenance.data_mode != "paper_trading"
            or provenance.broker_environment != "kis_paper"
            or provenance.account_scope_fingerprint is None
        ):
            raise PaperPortfolioLossUnavailable(
                "paper_loss_store_provenance_mismatch"
            )

    def _require_bound_provenance(
        self,
        provenance: StateStoreProvenance,
    ) -> None:
        self._require_paper_provenance(provenance)
        if (
            provenance.store_id != self._store_id
            or provenance.account_scope_fingerprint
            != self._account_scope_fingerprint
        ):
            raise PaperPortfolioLossUnavailable(
                "paper_loss_store_provenance_mismatch"
            )

    def _aware_clock(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise PaperPortfolioLossUnavailable("paper_loss_clock_invalid")
        return value

    def _require_fresh_observation(
        self,
        *,
        observed_at: datetime,
        received_at: datetime,
    ) -> None:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise PaperPortfolioLossUnavailable(
                "paper_balance_observation_invalid"
            )
        age = (received_at - observed_at).total_seconds()
        if age < -self._future_tolerance_seconds:
            raise PaperPortfolioLossUnavailable(
                "paper_balance_observation_future"
            )
        if age > self._balance_max_age_seconds:
            raise PaperPortfolioLossUnavailable(
                "paper_balance_observation_stale"
            )

    def _require_consistent_session(
        self,
        *,
        observed_at: datetime,
        received_at: datetime,
    ) -> date:
        observed_session = self._session_authority.current_open_session_date(
            observed_at
        )
        received_session = self._session_authority.current_open_session_date(
            received_at
        )
        if observed_session is None or received_session is None:
            raise PaperPortfolioLossUnavailable(
                "paper_loss_session_unavailable"
            )
        if observed_session != received_session:
            raise PaperPortfolioLossUnavailable(
                "paper_loss_session_changed"
            )
        return received_session

    def _require_baseline(
        self,
        baseline: PaperPortfolioLossBaseline,
        *,
        provenance: StateStoreProvenance,
        business_date: date,
        observed_at: datetime,
    ) -> None:
        if (
            baseline.store_id != provenance.store_id
            or baseline.data_mode != provenance.data_mode
            or baseline.broker_environment != provenance.broker_environment
            or baseline.account_scope_fingerprint
            != provenance.account_scope_fingerprint
        ):
            raise PaperPortfolioLossUnavailable(
                "paper_loss_baseline_provenance_mismatch"
            )
        if (
            baseline.business_date != business_date
            or baseline.month_key != business_date.strftime("%Y-%m")
        ):
            raise PaperPortfolioLossUnavailable(
                "paper_loss_baseline_session_mismatch"
            )
        if baseline.captured_at > observed_at:
            raise PaperPortfolioLossUnavailable(
                "paper_loss_baseline_not_effective"
            )
        if baseline.source == "manual_confirmed":
            if (
                baseline.confirmed_at is None
                or baseline.confirmed_at > observed_at
            ):
                raise PaperPortfolioLossUnavailable(
                    "paper_loss_baseline_not_effective"
                )
        elif baseline.source_business_date.strftime("%Y-%m") != baseline.month_key:
            raise PaperPortfolioLossUnavailable(
                "paper_loss_month_rollover_unconfirmed"
            )

    @staticmethod
    def _net_assets(balance: KisBalanceResult) -> Decimal:
        if (
            isinstance(balance.pages_fetched, bool)
            or balance.pages_fetched < 1
            or balance.transaction_id != KIS_BALANCE_TR_ID
        ):
            raise PaperPortfolioLossUnavailable("paper_balance_evidence_invalid")
        value = balance.summary.net_asset_amount
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise PaperPortfolioLossUnavailable("paper_balance_net_assets_invalid")
        return value

    @staticmethod
    def _loss_ratio(current: Decimal, baseline: float) -> float:
        try:
            denominator = Decimal(str(baseline))
            ratio = (current - denominator) / denominator
            result = float(ratio)
        except (InvalidOperation, OverflowError, ValueError, ZeroDivisionError):
            raise PaperPortfolioLossUnavailable(
                "paper_loss_calculation_invalid"
            ) from None
        if not isfinite(result) or result < -1:
            raise PaperPortfolioLossUnavailable(
                "paper_loss_calculation_invalid"
            )
        return result
