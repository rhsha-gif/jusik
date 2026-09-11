"""Run the frozen intraday study; legacy daily results remain reference-only."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from quantpilot.paper.intraday.evaluation import Experiment, run_study


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path.home() / ".quantpilot" / "intraday-v2" / "market.sqlite3",
    )
    parser.add_argument(
        "--experiment",
        type=Path,
        default=Path.home() / ".quantpilot" / "intraday-v2" / "research.sqlite3",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def external_path(path):
    path = path.expanduser().resolve()
    if any((p / ".git").exists() for p in (path.parent, *path.parents)):
        raise ValueError("intraday_runtime_path_inside_repository")
    return path


def run(args):
    from quantpilot.paper.intraday.data import IntradayData

    data = IntradayData(external_path(args.ledger))
    experiment = Experiment(external_path(args.experiment))
    try:
        result = run_study(
            data.dataset(include_events=False), experiment, datetime.now(timezone.utc)
        )
        if args.output:
            output = external_path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
        return result
    finally:
        experiment.close()
        data.close()


def main(argv=None):
    try:
        result = run(parse_args(argv))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "reason": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
