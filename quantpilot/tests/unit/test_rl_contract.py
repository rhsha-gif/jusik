from __future__ import annotations

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.rl.outputs import RLOutput, RLOutputType, TargetWeightDelta


def test_rl_delta_grid_is_bounded_and_zero_is_allowed() -> None:
    assert TargetWeightDelta(symbol="AAA", target_weight_delta=0.0).target_weight_delta == 0.0
    assert TargetWeightDelta(symbol="AAA", target_weight_delta=0.05).target_weight_delta == 0.05

    with pytest.raises(ValidationError):
        TargetWeightDelta(symbol="AAA", target_weight_delta=0.03)


def test_rl_output_schema_only_allows_delta_or_strategy_selection_payloads() -> None:
    output = RLOutput(
        output_type=RLOutputType.target_weight_delta,
        payload={"symbol": "AAA", "target_weight_delta": -0.02},
    )

    assert output.output_type == RLOutputType.target_weight_delta
    with pytest.raises(ValidationError):
        RLOutput(output_type=RLOutputType.target_weight_delta, payload={"symbol": "AAA", "action": "BUY"})
    with pytest.raises(ValidationError):
        RLOutput(output_type=RLOutputType.strategy_selection, payload={"strategy_id": "x", "quantity": 10})
