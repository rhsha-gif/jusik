from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from quantpilot.packages.core.schemas import PullbackTrendDecisionRules
from quantpilot.packages.core.strategies.promotion import compute_spec_hash
from quantpilot.packages.core.strategies.loader import default_strategy_dir, load_strategy_recipe


def test_pullback_trend_v2_loads_as_draft_and_not_executable() -> None:
    recipe = load_strategy_recipe("pullback_trend_v2")
    raw_recipe = yaml.safe_load(
        (default_strategy_dir() / "pullback_trend_v2.yaml").read_text(encoding="utf-8")
    )
    locked_rules = PullbackTrendDecisionRules()

    assert recipe.strategy_id == "pullback_trend_v2"
    assert recipe.promotion_status == "draft"
    assert recipe.allowed_execution_levels == []
    assert recipe.decision_rules == locked_rules
    assert raw_recipe["decision_rules"] == locked_rules.model_dump(mode="json")


def test_pullback_trend_v1_serialization_and_hash_remain_unchanged() -> None:
    recipe = load_strategy_recipe("pullback_trend_v1")
    dumped = recipe.model_dump(mode="json")

    assert recipe.decision_rules is None
    assert "decision_rules" not in dumped
    assert compute_spec_hash(recipe) == (
        "sha256:545fdf143ef2e35cbbb30e44db769eed893b848c9097fd6194eb8daba7ccfaa2"
    )


def test_pullback_decision_rules_reject_inconsistent_windows() -> None:
    with pytest.raises(ValidationError, match="risk_window must be shorter than trend_window"):
        PullbackTrendDecisionRules(risk_window=120)


@pytest.mark.parametrize(
    "mutation, error",
    [
        (lambda payload: payload.pop("decision_rules"), "requires decision_rules"),
        (
            lambda payload: payload["decision_rules"].update({"oversold_rsi": 36.0}),
            "locked pullback_trend_v2 thresholds",
        ),
        (
            lambda payload: payload["decision_rules"].update({"unknown_threshold": 1}),
            "Extra inputs are not permitted",
        ),
    ],
)
def test_pullback_trend_v2_rejects_missing_modified_or_unknown_rules(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], object],
    error: str,
) -> None:
    recipe = load_strategy_recipe("pullback_trend_v2")
    payload = recipe.model_dump(mode="json")
    mutation(payload)
    path = tmp_path / "malformed_v2.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValidationError, match=error):
        load_strategy_recipe("malformed_v2", strategy_dir=tmp_path)


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
