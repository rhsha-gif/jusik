from __future__ import annotations

from pathlib import Path

import yaml

from quantpilot.packages.core.schemas import StrategyRecipe


def default_strategy_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "strategy_specs"


def load_strategy_recipe(strategy_id: str, *, strategy_dir: Path | None = None) -> StrategyRecipe:
    directory = strategy_dir or default_strategy_dir()
    path = directory / f"{strategy_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"strategy recipe not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return StrategyRecipe.model_validate(data)


def load_default_strategy() -> StrategyRecipe:
    return load_strategy_recipe("pullback_trend_v1")
