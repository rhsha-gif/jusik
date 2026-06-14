from __future__ import annotations

import json
from pathlib import Path

from quantpilot.packages.core.execution.fallback_manager import FallbackManager


def test_fallback_manager_maps_known_level5_blockers() -> None:
    cases = json.loads((Path(__file__).parents[1] / "fixtures" / "operator_fallback_cases.json").read_text(encoding="utf-8"))
    manager = FallbackManager()

    for case in cases:
        decision = manager.for_reason(case["reason_code"])
        assert decision.from_level == case["from_level"]
        assert decision.to_level == case["to_level"]
        assert decision.order_submission_enabled is case["order_submission_enabled"]


def test_unknown_blocker_degrades_to_safest_noop() -> None:
    manager = FallbackManager()

    decision = manager.for_reason("totally_unexpected_condition")

    assert decision.from_level == 5
    assert decision.to_level == 0
    assert decision.order_submission_enabled is False
    assert "totally_unexpected_condition" in decision.detail


def test_no_fallback_ever_enables_order_submission() -> None:
    from quantpilot.packages.core.execution.fallback_manager import FALLBACK_MATRIX

    manager = FallbackManager()
    for reason_code in FALLBACK_MATRIX:
        assert manager.for_reason(reason_code).order_submission_enabled is False


def test_level5_authority_check_fallback_mappings_are_known_reason_codes() -> None:
    from quantpilot.packages.core.execution.fallback_manager import FALLBACK_MATRIX
    from quantpilot.packages.core.operator.service import CHECK_TO_FALLBACK_REASON

    assert set(CHECK_TO_FALLBACK_REASON.values()) <= set(FALLBACK_MATRIX)
