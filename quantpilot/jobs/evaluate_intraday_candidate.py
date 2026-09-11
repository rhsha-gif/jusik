"""Record an adjusted evidence look; optional admission never starts trading."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from quantpilot.jobs.run_intraday_backtest import external_path
from quantpilot.paper.config import STRATEGIES
from quantpilot.paper.intraday.evaluation import Experiment, evaluate_candidate


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        type=Path,
        default=Path.home() / ".quantpilot" / "intraday-v2" / "research.sqlite3",
    )
    parser.add_argument("--strategy", required=True, choices=STRATEGIES)
    parser.add_argument("--dataset-hash", required=True)
    parser.add_argument("--admit-runtime", type=Path)
    return parser.parse_args(argv)


def run(args):
    experiment = Experiment(external_path(args.experiment))
    now = datetime.now(timezone.utc)
    try:
        result = evaluate_candidate(experiment, args.strategy, args.dataset_hash, now)
        if args.admit_runtime:
            from quantpilot.paper.store import Store
            from quantpilot.paper.intraday.deployment import admit

            store = Store(external_path(args.admit_runtime) / "experiment.sqlite3")
            try:
                admit(store, experiment, result, now)
            finally:
                store.close()
        return result
    finally:
        experiment.close()


def main(argv=None):
    try:
        print(json.dumps(run(parse_args(argv)), ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "reason": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
