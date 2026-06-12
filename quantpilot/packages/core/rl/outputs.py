from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


class RLOutputType(str, Enum):
    target_weight_delta = "target_weight_delta"
    strategy_selection = "strategy_selection"


class RLOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_type: RLOutputType
    payload: dict[str, Any]
