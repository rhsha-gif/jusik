from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quantpilot.jobs.run_kis_paper_kill import (
    ENGAGE_CONFIRMATION,
    RELEASE_CONFIRMATION,
    KisPaperKillConfig,
    PaperKillJobError,
    paper_kill_gate_reason,
    run_from_environment,
)
from quantpilot.packages.core.kis_paper import (
    KisBalanceResult,
    KisBalanceSummary,
    KisCancelableOrder,
    KisCancelableOrdersResult,
    paper_account_scope_fingerprint,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


NOW = datetime(2026, 7, 11, 1, 0, tzinfo=timezone.utc)


def _environment(tmp_path, *, confirmation: str = ENGAGE_CONFIRMATION):
    return {
        "KIS_PAPER_KILL_ENABLED": "true",
        "KIS_PAPER_KILL_CONFIRMATION": confirmation,
        "LIVE_TRADING_ENABLED": "false",
        "MARKET_ORDERS_ENABLED": "false",
        "BROKER_MODE": "paper",
        "DATA_MODE": "paper_trading",
        "KIS_PAPER_STATE_DB": str((tmp_path / "paper.sqlite3").resolve()),
        "KIS_PAPER_APP_KEY": "secret-app-key",
        "KIS_PAPER_APP_SECRET": "secret-app-secret",
        "KIS_PAPER_ACCOUNT_NUMBER": "12345678",
        "KIS_PAPER_PRODUCT_CODE": "01",
        "KIS_PAPER_ACCESS_TOKEN": "secret-access-token",
    }


class _Clock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        self.current += timedelta(microseconds=10)
        return self.current


class _Client:
    account_scope_fingerprint = paper_account_scope_fingerprint("12345678", "01")

    def get_balance(self, *, exchange: str = "KRX") -> KisBalanceResult:
        assert exchange == "KRX"
        return KisBalanceResult(
            positions=(),
            summary=KisBalanceSummary(
                deposit_amount=Decimal("1000000"),
                next_day_settlement_amount=Decimal("1000000"),
                total_purchase_amount=Decimal("0"),
                total_evaluation_amount=Decimal("0"),
                net_asset_amount=Decimal("1000000"),
                evaluation_profit_loss=Decimal("0"),
            ),
            pages_fetched=1,
        )

    def get_cancelable_orders(self) -> KisCancelableOrdersResult:
        return KisCancelableOrdersResult(rows=(), pages_fetched=1)


def test_kill_gate_is_disabled_by_default_and_rejects_unsafe_flags() -> None:
    assert paper_kill_gate_reason("engage", {}) == "paper_kill_disabled"
    enabled = {
        "KIS_PAPER_KILL_ENABLED": "true",
        "KIS_PAPER_KILL_CONFIRMATION": ENGAGE_CONFIRMATION,
        "BROKER_MODE": "paper",
        "LIVE_TRADING_ENABLED": "true",
    }
    assert paper_kill_gate_reason("engage", enabled) == "live_trading_flag_engaged"
    enabled["LIVE_TRADING_ENABLED"] = "false"
    enabled["MARKET_ORDERS_ENABLED"] = "true"
    assert paper_kill_gate_reason("engage", enabled) == "market_orders_flag_engaged"
    enabled["MARKET_ORDERS_ENABLED"] = "false"
    assert paper_kill_gate_reason("engage", enabled) == "paper_data_mode_required"


def test_kill_config_requires_action_specific_confirmation_and_redacts_secrets(
    tmp_path,
) -> None:
    environment = _environment(tmp_path)
    config = KisPaperKillConfig.from_environment("engage", environment)
    rendered = repr(config)
    assert "secret-app-key" not in rendered
    assert "secret-access-token" not in rendered

    with pytest.raises(PaperKillJobError, match="confirmation"):
        KisPaperKillConfig.from_environment("release", environment)


def test_engage_and_release_run_without_normal_autonomy_flags(tmp_path) -> None:
    environment = _environment(tmp_path)
    clock = _Clock()
    engaged = run_from_environment(
        "engage",
        environment=environment,
        client_builder=lambda _config: _Client(),  # type: ignore[arg-type]
        clock=clock,
    )
    assert engaged.status == "killed"

    environment["KIS_PAPER_KILL_CONFIRMATION"] = RELEASE_CONFIRMATION
    released = run_from_environment(
        "release",
        environment=environment,
        client_builder=lambda _config: _Client(),  # type: ignore[arg-type]
        clock=clock,
    )
    assert released.status == "released"

    with PaperStateStore(
        environment["KIS_PAPER_STATE_DB"],
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint=_Client.account_scope_fingerprint,
    ) as store:
        assert store.paper_kill_blocks_submission() is False


def _identity_row(original_order_number: str) -> KisCancelableOrder:
    return KisCancelableOrder(
        order_branch_number="91252",
        order_number="0000012345",
        original_order_number=original_order_number,
        order_division_name="지정가",
        symbol="005930",
        product_name="삼성전자",
        revision_cancel_division_name="",
        order_quantity=3,
        order_price=Decimal("70000"),
        order_time="103012",
        total_filled_quantity=0,
        total_filled_amount=Decimal("0"),
        cancelable_quantity=3,
        side="buy",
        order_division_code="00",
        exchange_division_code="KRX",
        exchange_id="KRX",
    )


@pytest.mark.parametrize("sentinel", ["", "0", "0000000000"])
def test_emergency_cancel_recognises_every_kis_original_order_sentinel(sentinel) -> None:
    """The paper server reports ten zeroes; the kill path must use the reconciler's rule."""
    from types import SimpleNamespace
    from quantpilot.packages.core.execution.paper_kill import _cancel_identity_matches

    dispatch = SimpleNamespace(
        broker_order_reference="0000012345",
        broker_forwarding_order_org_number="91252",
        broker_order_branch_number="91252",
        broker_business_date=NOW.date(),
        broker_order_time="103012",
        symbol="005930",
        side="buy",
        quantity=3.0,
        cumulative_filled_quantity=0.0,
        fill_evidence=(),
        limit_price=70000.0,
    )
    assert _cancel_identity_matches(dispatch, _identity_row(sentinel), business_date=NOW.date())
    # A nonzero original number denotes a correction/cancel child, never our original order.
    assert not _cancel_identity_matches(dispatch, _identity_row("0000012300"), business_date=NOW.date())
    # An otherwise matching external order still fails on identity, not on the sentinel.
    other = SimpleNamespace(**{**dispatch.__dict__, "broker_order_reference": "0000099999"})
    assert not _cancel_identity_matches(other, _identity_row(sentinel), business_date=NOW.date())
