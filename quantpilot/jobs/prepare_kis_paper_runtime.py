"""Prepare local paper artifacts without broker requests or trading authority."""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path

from quantpilot.jobs.check_kis_paper_connection import connection_config
from quantpilot.packages.core.schemas import UserPolicy
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.strategies.promotion import compute_spec_hash
from quantpilot.packages.core.strategies.registry import StrategyRegistryEntry
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


def _write_new(path: Path, payload: object) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
    except FileExistsError:
        pass


def prepare_runtime(root: Path, environment: Mapping[str, str]) -> dict[str, object]:
    root = root.expanduser().resolve()
    repository = Path(__file__).resolve().parents[2]
    if root == repository or repository in root.parents:
        raise ValueError("paper runtime must be outside the repository")
    config = connection_config(environment)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    if root == repository or repository in root.parents:
        raise ValueError("paper runtime must be outside the repository")
    # Reject existing child links that could redirect artifacts into the repository.
    for name in ("state.sqlite3", "policy.draft.json", "registry.draft.json", "runtime.json", "historical"):
        target = root / name
        if target.resolve().parent != root:
            raise ValueError("paper runtime child path escapes its directory")
    with PaperStateStore(
        root / "state.sqlite3", data_mode="paper_trading", broker_environment="kis_paper",
        account_scope_fingerprint=config.account_scope_fingerprint,
    ) as store:
        schema_version = store.provenance.schema_version
    (root / "historical").mkdir(exist_ok=True)
    policy = UserPolicy(user_id="local-paper-operator", kill_switch_engaged=True)
    recipe = load_strategy_recipe("pullback_trend_v2")
    entry = StrategyRegistryEntry(
        strategy_id=recipe.strategy_id, version=recipe.version,
        spec_hash=compute_spec_hash(recipe), status="draft", allowed_execution_levels=[],
    )
    _write_new(root / "policy.draft.json", policy.model_dump(mode="json"))
    _write_new(root / "registry.draft.json", {
        "entries": [entry.model_dump(mode="json")], "lifecycle_records": [],
    })
    _write_new(root / "runtime.json", {
        "purpose": "paper_preparation_only",
        "paths": {
            "state_db": str(root / "state.sqlite3"),
            "draft_policy": str(root / "policy.draft.json"),
            "draft_registry": str(root / "registry.draft.json"),
            "historical_data": str(root / "historical"),
        },
        "activation_requirements": [
            "user_policy_limits_and_universe", "validated_local_historical_data",
            "strategy_promotion_evidence_and_human_review", "confirmed_prior_close_and_month_start_equity",
            "approved_business_date", "explicit_paper_session_activation",
        ],
    })
    return {
        "status": "prepared_not_armed", "data_mode": "paper_trading",
        "runtime_directory": str(root), "schema_version": schema_version,
        "note": "Existing artifacts are preserved; this is not an execution readiness verdict.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare_runtime(args.runtime_dir, os.environ)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
