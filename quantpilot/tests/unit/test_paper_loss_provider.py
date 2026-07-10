from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quantpilot.packages.core.kis_paper import (
    KisBalanceResult,
    KisBalanceSummary,
)
from quantpilot.packages.core.operator.paper_loss import (
    PaperPortfolioLossUnavailable,
    PersistentPaperPortfolioLossProvider,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperPortfolioLossBaseline,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


KST = ZoneInfo("Asia/Seoul")
OBSERVED_AT = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
BUSINESS_DATE = date(2026, 7, 10)
ACCOUNT = "sha256:" + "a" * 64


class StaticSessionAuthority:
    def __init__(self, session_dates: list[date | None] | None = None) -> None:
        self._session_dates = list(session_dates or [BUSINESS_DATE])
        self.calls: list[datetime] = []

    def current_open_session_date(self, observed_at: datetime) -> date | None:
        self.calls.append(observed_at)
        index = min(len(self.calls) - 1, len(self._session_dates) - 1)
        return self._session_dates[index]


def _store(path) -> PaperStateStore:
    return PaperStateStore(
        path,
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint=ACCOUNT,
    )


def _baseline(
    store: PaperStateStore,
    **updates: object,
) -> PaperPortfolioLossBaseline:
    values: dict[str, object] = {
        "store_id": store.provenance.store_id,
        "account_scope_fingerprint": ACCOUNT,
        "business_date": BUSINESS_DATE,
        "month_key": "2026-07",
        "prior_close_equity": 1_000_000.0,
        "month_start_equity": 1_100_000.0,
        "source": "manual_confirmed",
        "source_business_date": date(2026, 7, 9),
        "captured_at": datetime(2026, 7, 10, 9, 0, tzinfo=KST),
        "confirmed_at": datetime(2026, 7, 10, 9, 1, tzinfo=KST),
    }
    values.update(updates)
    return PaperPortfolioLossBaseline(**values)


def _balance(
    net_assets: str = "990000",
    *,
    pages_fetched: int = 1,
    transaction_id: str = "VTTC8434R",
) -> KisBalanceResult:
    return KisBalanceResult(
        positions=(),
        summary=KisBalanceSummary(
            deposit_amount=Decimal(net_assets),
            next_day_settlement_amount=Decimal(net_assets),
            total_purchase_amount=Decimal("0"),
            total_evaluation_amount=Decimal("0"),
            net_asset_amount=Decimal(net_assets),
            evaluation_profit_loss=Decimal("0"),
        ),
        pages_fetched=pages_fetched,
        transaction_id=transaction_id,
    )


def _provider(
    store: PaperStateStore,
    *,
    authority: StaticSessionAuthority | None = None,
    now: datetime = OBSERVED_AT + timedelta(seconds=2),
    max_age: int = 60,
) -> PersistentPaperPortfolioLossProvider:
    return PersistentPaperPortfolioLossProvider(
        store,
        session_authority=authority or StaticSessionAuthority(),
        clock=lambda: now,
        balance_max_age_seconds=max_age,
    )


def test_calculates_daily_and_monthly_ratios_from_durable_baseline(
    tmp_path,
) -> None:
    with _store(tmp_path / "paper.sqlite3") as store:
        store.insert_paper_portfolio_loss_baseline(_baseline(store))
        provider = _provider(store)

        metrics = provider.get_loss_metrics(
            _balance(),
            observed_at=OBSERVED_AT,
        )

        assert metrics.daily_loss_ratio == pytest.approx(-0.01)
        assert metrics.monthly_loss_ratio == pytest.approx(-0.1)
        assert metrics.as_of == OBSERVED_AT
        assert provider.account_scope_fingerprint == ACCOUNT


def test_missing_first_session_baseline_fails_closed_without_creating_one(
    tmp_path,
) -> None:
    with _store(tmp_path / "paper.sqlite3") as store:
        provider = _provider(store)

        with pytest.raises(
            PaperPortfolioLossUnavailable,
            match="^paper_loss_baseline_missing$",
        ) as caught:
            provider.get_loss_metrics(_balance(), observed_at=OBSERVED_AT)

        assert caught.value.reason_code == "paper_loss_baseline_missing"
        assert store.list_paper_portfolio_loss_baselines() == []


@pytest.mark.parametrize(
    ("observed_at", "now", "reason"),
    [
        (
            OBSERVED_AT,
            OBSERVED_AT + timedelta(seconds=61),
            "paper_balance_observation_stale",
        ),
        (
            OBSERVED_AT + timedelta(seconds=2),
            OBSERVED_AT,
            "paper_balance_observation_future",
        ),
        (
            OBSERVED_AT.replace(tzinfo=None),
            OBSERVED_AT,
            "paper_balance_observation_invalid",
        ),
    ],
)
def test_stale_future_and_naive_balance_observations_are_blocked(
    tmp_path,
    observed_at: datetime,
    now: datetime,
    reason: str,
) -> None:
    with _store(tmp_path / "paper.sqlite3") as store:
        provider = _provider(store, now=now)

        with pytest.raises(PaperPortfolioLossUnavailable, match=f"^{reason}$"):
            provider.get_loss_metrics(_balance(), observed_at=observed_at)


@pytest.mark.parametrize(
    "balance",
    [
        _balance("NaN"),
        _balance("0"),
        _balance(pages_fetched=0),
        _balance(transaction_id="unexpected"),
    ],
)
def test_invalid_or_mismatched_broker_balance_evidence_is_blocked(
    tmp_path,
    balance: KisBalanceResult,
) -> None:
    with _store(tmp_path / "paper.sqlite3") as store:
        store.insert_paper_portfolio_loss_baseline(_baseline(store))
        provider = _provider(store)

        with pytest.raises(PaperPortfolioLossUnavailable, match="paper_balance"):
            provider.get_loss_metrics(balance, observed_at=OBSERVED_AT)


def test_closed_or_changed_authoritative_session_is_blocked(tmp_path) -> None:
    cases = [
        ([None, None], "paper_loss_session_unavailable"),
        (
            [BUSINESS_DATE, BUSINESS_DATE + timedelta(days=1)],
            "paper_loss_session_changed",
        ),
    ]
    with _store(tmp_path / "paper.sqlite3") as store:
        for session_dates, reason in cases:
            provider = _provider(
                store,
                authority=StaticSessionAuthority(session_dates),
            )
            with pytest.raises(
                PaperPortfolioLossUnavailable,
                match=f"^{reason}$",
            ):
                provider.get_loss_metrics(_balance(), observed_at=OBSERVED_AT)


def test_manual_baseline_confirmation_must_precede_balance_observation(
    tmp_path,
) -> None:
    with _store(tmp_path / "paper.sqlite3") as store:
        store.insert_paper_portfolio_loss_baseline(
            _baseline(
                store,
                confirmed_at=OBSERVED_AT + timedelta(minutes=1),
            )
        )
        provider = _provider(store)

        with pytest.raises(
            PaperPortfolioLossUnavailable,
            match="^paper_loss_baseline_not_effective$",
        ):
            provider.get_loss_metrics(_balance(), observed_at=OBSERVED_AT)


def test_month_rollover_requires_manually_confirmed_baseline(tmp_path) -> None:
    rollover_date = date(2026, 8, 3)
    rollover_observed = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
    with _store(tmp_path / "paper.sqlite3") as store:
        baseline = _baseline(
            store,
            business_date=rollover_date,
            month_key="2026-08",
            source_business_date=date(2026, 7, 31),
            captured_at=datetime(2026, 8, 3, 9, 0, tzinfo=KST),
            confirmed_at=datetime(2026, 8, 3, 9, 1, tzinfo=KST),
        )
        store.insert_paper_portfolio_loss_baseline(baseline)
        provider = _provider(
            store,
            authority=StaticSessionAuthority([rollover_date]),
            now=rollover_observed + timedelta(seconds=1),
        )

        metrics = provider.get_loss_metrics(
            _balance(),
            observed_at=rollover_observed,
        )

        assert metrics.daily_loss_ratio == pytest.approx(-0.01)
        assert metrics.monthly_loss_ratio == pytest.approx(-0.1)


def test_fixture_store_is_rejected_before_any_balance_is_used(tmp_path) -> None:
    with PaperStateStore(tmp_path / "fixture.sqlite3") as store:
        with pytest.raises(
            PaperPortfolioLossUnavailable,
            match="^paper_loss_store_provenance_mismatch$",
        ):
            PersistentPaperPortfolioLossProvider(
                store,
                session_authority=StaticSessionAuthority(),
                clock=lambda: OBSERVED_AT,
            )
