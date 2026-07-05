"""Confirmed transaction-cost assumptions for research backtests.

Basis (user decision, 2026-07-06): KIS (한국투자증권) real-trading open API
for a retail individual investor.

- Commission: the KIS open API charges the same commission as HTS with no
  separate API fee. BanKIS (bank-linked) accounts pay 0.0140527% per side
  online; branch-opened accounts pay 0.147% online — pass ``--fee-bps 14.7``
  in that case.
- Sell tax: from 2026-01-01 the KRX securities transaction tax reverted to
  the 2023 schedule — KOSPI 0.05% + rural special tax 0.15%, KOSDAQ 0.20% —
  both 0.20% of sell notional.
- Capital gains: on-exchange sales by minority individual holders are not
  taxed (the financial investment income tax was repealed), so the model has
  no CGT term. Dividend withholding (15.4%) is out of scope: the replay
  engine models price returns only.
- Slippage stays a research assumption (not broker-confirmed).
"""

from __future__ import annotations

from typing import Any

from quantpilot.packages.core.backtest.schemas import BacktestAssumptions

KIS_BANKIS_ONLINE_FEE_BPS = 1.40527
KRX_SELL_TAX_BPS_FROM_2026 = 20.0
RESEARCH_SLIPPAGE_BPS = 5.0

KIS_RETAIL_COST_BASIS = "kis_bankis_online_api+krx_tax_2026"


def kis_retail_assumptions(**overrides: Any) -> BacktestAssumptions:
    """Build ``BacktestAssumptions`` on the confirmed KIS retail cost basis."""
    fields: dict[str, Any] = {
        "fee_bps": KIS_BANKIS_ONLINE_FEE_BPS,
        "slippage_bps": RESEARCH_SLIPPAGE_BPS,
        "sell_tax_bps": KRX_SELL_TAX_BPS_FROM_2026,
    }
    fields.update(overrides)
    return BacktestAssumptions(**fields)


def cost_basis_label(assumptions: BacktestAssumptions) -> str:
    """Name the cost basis so reports show whether the preset was overridden."""
    if (
        assumptions.fee_bps == KIS_BANKIS_ONLINE_FEE_BPS
        and assumptions.sell_tax_bps == KRX_SELL_TAX_BPS_FROM_2026
    ):
        return KIS_RETAIL_COST_BASIS
    return "custom_override"
