from __future__ import annotations

from quantpilot.jobs.run_local_backtest import parse_args
from quantpilot.packages.core.backtest.costs import (
    KIS_BANKIS_ONLINE_FEE_BPS,
    KIS_RETAIL_COST_BASIS,
    KRX_SELL_TAX_BPS_FROM_2026,
    RESEARCH_SLIPPAGE_BPS,
    cost_basis_label,
    kis_retail_assumptions,
)


def test_kis_retail_preset_matches_confirmed_rates() -> None:
    assumptions = kis_retail_assumptions()
    # KIS BanKIS online commission 0.0140527% per side; API adds no extra fee.
    assert assumptions.fee_bps == 1.40527
    # KRX 2026 schedule: KOSPI 0.05% + rural special 0.15%, KOSDAQ 0.20%.
    assert assumptions.sell_tax_bps == 20.0
    assert assumptions.slippage_bps == RESEARCH_SLIPPAGE_BPS
    assert cost_basis_label(assumptions) == KIS_RETAIL_COST_BASIS


def test_preset_overrides_are_labelled_custom() -> None:
    branch_account = kis_retail_assumptions(fee_bps=14.7)
    assert branch_account.fee_bps == 14.7
    assert cost_basis_label(branch_account) == "custom_override"


def test_local_backtest_job_defaults_to_kis_retail_basis() -> None:
    args = parse_args(["--data-dir", "unused"])
    assert args.fee_bps == KIS_BANKIS_ONLINE_FEE_BPS
    assert args.sell_tax_bps == KRX_SELL_TAX_BPS_FROM_2026
    assert args.slippage_bps == RESEARCH_SLIPPAGE_BPS
