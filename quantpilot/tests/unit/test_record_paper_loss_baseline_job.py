from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from quantpilot.jobs.record_paper_loss_baseline import (
    BASELINE_CONFIRMATION,
    PaperBaselineConfig,
    PaperBaselineError,
    record_baseline,
)
from quantpilot.packages.core.kis_paper import (
    paper_account_scope_fingerprint,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)


def _environment(tmp_path) -> dict[str, str]:
    return {
        "KIS_PAPER_BASELINE_CONFIRMATION": BASELINE_CONFIRMATION,
        "LIVE_TRADING_ENABLED": "false",
        "KIS_PAPER_STATE_DB": str((tmp_path / "paper.sqlite3").resolve()),
        "KIS_PAPER_APPROVED_BUSINESS_DATE": "2026-07-10",
        "KIS_PAPER_BASELINE_SOURCE_DATE": "2026-07-09",
        "KIS_PAPER_PRIOR_CLOSE_EQUITY": "1000000",
        "KIS_PAPER_MONTH_START_EQUITY": "1100000",
        "KIS_PAPER_ACCOUNT_NUMBER": "12345678",
        "KIS_PAPER_PRODUCT_CODE": "01",
    }


def test_manual_confirmation_is_mandatory_before_parsing_values(tmp_path) -> None:
    environment = _environment(tmp_path)
    environment["KIS_PAPER_BASELINE_CONFIRMATION"] = "yes"

    with pytest.raises(
        PaperBaselineError,
        match="^paper_loss_baseline_confirmation_required$",
    ):
        PaperBaselineConfig.from_environment(environment)


def test_config_hides_account_and_equity_values_from_repr(tmp_path) -> None:
    config = PaperBaselineConfig.from_environment(_environment(tmp_path))

    rendered = repr(config)
    assert "12345678" not in rendered
    assert "1000000" not in rendered
    assert "1100000" not in rendered


def test_records_one_immutable_manual_baseline_and_replays_exact_input(
    tmp_path,
) -> None:
    config = PaperBaselineConfig.from_environment(_environment(tmp_path))

    first = record_baseline(config, confirmed_at=NOW)
    replay = record_baseline(
        config,
        confirmed_at=NOW + timedelta(minutes=1),
    )

    assert replay == first
    assert first.business_date == date(2026, 7, 10)
    fingerprint = paper_account_scope_fingerprint("12345678", "01")
    with PaperStateStore(
        config.database_path,
        data_mode="paper_trading",
        account_scope_fingerprint=fingerprint,
    ) as store:
        assert store.load_paper_portfolio_loss_baseline(
            date(2026, 7, 10)
        ) == first


def test_conflicting_manual_values_never_overwrite_existing_baseline(
    tmp_path,
) -> None:
    config = PaperBaselineConfig.from_environment(_environment(tmp_path))
    record_baseline(config, confirmed_at=NOW)
    conflicting = PaperBaselineConfig(
        **{
            **config.__dict__,
            "prior_close_equity": 999_000,
        }
    )

    with pytest.raises(
        PaperBaselineError,
        match="^paper_loss_baseline_conflict$",
    ):
        record_baseline(conflicting, confirmed_at=NOW)


def test_confirmation_must_occur_on_the_approved_kst_date(tmp_path) -> None:
    config = PaperBaselineConfig.from_environment(_environment(tmp_path))

    with pytest.raises(
        PaperBaselineError,
        match="^paper_loss_baseline_date_not_current$",
    ):
        record_baseline(
            config,
            confirmed_at=datetime(2026, 7, 9, 1, 0, tzinfo=timezone.utc),
        )
