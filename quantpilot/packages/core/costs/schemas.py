from __future__ import annotations

from pydantic import Field

from quantpilot.packages.core.schemas import HarnessModel


class TransactionCostAssumption(HarnessModel):
    cost_model_id: str
    market: str
    commission_bps_per_side: float = Field(ge=0)
    slippage_bps_per_side: float = Field(ge=0)
    sell_tax_bps: float = Field(default=0.0, ge=0)
    sell_regulatory_fee_bps: float = Field(default=0.0, ge=0)
    fx_spread_bps_round_trip: float = Field(default=0.0, ge=0)
    description: str

    @property
    def round_trip_bps(self) -> float:
        return (
            2 * self.commission_bps_per_side
            + 2 * self.slippage_bps_per_side
            + self.sell_tax_bps
            + self.sell_regulatory_fee_bps
            + self.fx_spread_bps_round_trip
        )
