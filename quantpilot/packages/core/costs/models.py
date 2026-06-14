from __future__ import annotations

from quantpilot.packages.core.costs.schemas import TransactionCostAssumption


_COST_MODELS: dict[str, TransactionCostAssumption] = {
    "kr_stock_fixture_v1": TransactionCostAssumption(
        cost_model_id="kr_stock_fixture_v1",
        market="KR_STOCK",
        commission_bps_per_side=1.5,
        slippage_bps_per_side=5.0,
        sell_tax_bps=18.0,
        sell_regulatory_fee_bps=0.0,
        description="Deterministic fixture assumption for Korean stock backtests.",
    ),
    "us_stock_fixture_v1": TransactionCostAssumption(
        cost_model_id="us_stock_fixture_v1",
        market="US_STOCK",
        commission_bps_per_side=1.0,
        slippage_bps_per_side=6.0,
        sell_tax_bps=0.0,
        sell_regulatory_fee_bps=0.3,
        fx_spread_bps_round_trip=10.0,
        description="Deterministic fixture assumption for US stock backtests.",
    ),
}


def list_cost_models() -> list[TransactionCostAssumption]:
    return list(_COST_MODELS.values())


def get_cost_model(cost_model_id: str) -> TransactionCostAssumption:
    return _COST_MODELS[cost_model_id]
