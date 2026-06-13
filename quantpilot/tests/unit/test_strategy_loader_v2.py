from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.strategies.loader import load_strategy_recipe


def test_pullback_trend_v2_loads_as_draft_and_not_executable() -> None:
    recipe = load_strategy_recipe("pullback_trend_v2")

    assert recipe.strategy_id == "pullback_trend_v2"
    assert recipe.promotion_status == "draft"
    assert recipe.allowed_execution_levels == []


def test_strategy_loader_rejects_unearned_execution_level() -> None:
    tmp_dir = Path.cwd() / "quantpilot" / "tests" / ".tmp_strategy_loader"
    tmp_dir.mkdir(exist_ok=True)
    path = tmp_dir / "unsafe.yaml"
    path.write_text(
        "\n".join(
            [
                "strategy_id: unsafe",
                'version: "1.0"',
                "entry_rules: [fixture]",
                "exit_rules: [fixture]",
                "position_sizing:",
                "  method: capped_target_weight",
                "risk_rules: [fixture]",
                "rebalance: weekly",
                "promotion_status: draft",
                "allowed_execution_levels: [level_4]",
            ]
        ),
        encoding="utf-8",
    )

    try:
        with pytest.raises(ValidationError):
            load_strategy_recipe("unsafe", strategy_dir=tmp_dir)
    finally:
        path.unlink(missing_ok=True)
        tmp_dir.rmdir()
