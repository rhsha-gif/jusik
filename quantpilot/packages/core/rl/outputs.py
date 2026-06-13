from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RLOutputType(str, Enum):
    target_weight_delta = "target_weight_delta"
    strategy_selection = "strategy_selection"


class TargetWeightDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    target_weight_delta: float

    @field_validator("target_weight_delta")
    @classmethod
    def delta_must_be_on_grid(cls, value: float) -> float:
        allowed = {-0.05, -0.02, 0.0, 0.02, 0.05}
        if value not in allowed:
            raise ValueError("target_weight_delta must be one of -0.05, -0.02, 0, 0.02, 0.05")
        return value


class StrategySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str


class RLOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_type: RLOutputType
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def payload_must_match_output_type(self) -> "RLOutput":
        if self.output_type == RLOutputType.target_weight_delta:
            TargetWeightDelta.model_validate(self.payload)
        elif self.output_type == RLOutputType.strategy_selection:
            StrategySelection.model_validate(self.payload)
        return self
