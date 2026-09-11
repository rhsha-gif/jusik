"""Offline-safe entry point for importing or collecting intraday research data."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence

from quantpilot.paper.intraday.data import DATA_MODES, IntradayData


RANKING_SOURCE = "kis_paper_ranking"
MINUTES_SOURCE = "kis_paper_minutes"


def collect_once(
    ledger: IntradayData, market: object, now: datetime, limit: int = 20, *, clock=None
) -> dict:
    """Collect one PIT universe and completed bars from an injected PaperMarket.

    The function never constructs a client and never changes providers.  Both
    ``kis_paper_ranking`` and ``kis_paper_minutes`` provenance must have been
    registered by the caller.  Failures are returned as explicit issues.
    """

    issues: list[dict[str, str]] = []
    clock = clock or (lambda: now)
    if not isinstance(ledger, IntradayData):
        raise TypeError("ledger must be IntradayData")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer in [1, 100]")
    try:
        _, collected_at = _collection_time(now)
        candidates, source = market.candidates(now, limit)
        if source != RANKING_SOURCE:
            return {
                "status": "data_insufficient",
                "bars_recorded": 0,
                "universe_recorded": False,
                "issues": [
                    {"scope": "universe", "error": "kis_paper_ranking_required"}
                ],
            }
        if not isinstance(candidates, list):
            raise TypeError("candidate universe must be a list")
        if len(candidates) > limit:
            raise ValueError("candidate universe exceeds requested limit")
        ledger.record_universe(
            candidates,
            clock(),
            RANKING_SOURCE,
            {
                "selection": "volume_rank",
                "limit": limit,
                **(
                    {"eligibility": market.eligibility(candidates, clock())}
                    if hasattr(market, "eligibility")
                    else {}
                ),
            },
        )
    except Exception as exc:
        return {
            "status": "data_insufficient",
            "collected_at": _safe_timestamp(now),
            "bars_recorded": 0,
            "universe_recorded": False,
            "issues": [{"scope": "universe", "error": type(exc).__name__}],
        }

    bars_recorded = 0
    for symbol in candidates:
        try:
            bars = market.minutes(symbol, now)
            received_at = clock()
            if not isinstance(bars, list):
                raise TypeError("minutes must return a list")
            for bar in bars:
                if getattr(bar, "symbol", None) != symbol:
                    raise ValueError("minute bar symbol does not match candidate")
                bars_recorded += int(
                    ledger.record_bar(bar, received_at, MINUTES_SOURCE)
                )
        except Exception as exc:
            issues.append({"scope": str(symbol), "error": type(exc).__name__})
    return {
        "status": "collected" if not issues else "partial",
        "collected_at": collected_at,
        "bars_recorded": bars_recorded,
        "universe_recorded": True,
        "issues": issues,
    }


def _collection_time(value: datetime) -> tuple[datetime, str]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("now must be timezone-aware")
    normalized = value.astimezone(timezone.utc)
    return normalized, normalized.isoformat()


def _safe_timestamp(value: object) -> str | None:
    try:
        return _collection_time(value)[1]
    except (TypeError, ValueError):
        return None


def _summary(ledger: IntradayData, status: str, issues: list | None = None) -> dict:
    dataset = ledger.dataset()
    return {
        "status": status,
        "schema_version": dataset["schema_version"],
        "data_mode": dataset["data_mode"],
        "counts": {
            "bars": len(dataset["bars"]),
            "universes": len(dataset["universes"]),
            "events": len(dataset["events"]),
            "sources": len(dataset["provenance"]),
        },
        "sha256": dataset["sha256"],
        "issues": issues or [],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path.home() / ".quantpilot" / "intraday-v2" / "market.sqlite3",
        help="SQLite research ledger path (defaults outside the repository)",
    )
    parser.add_argument("--input", type=Path, help="strict dataset JSON bundle")
    parser.add_argument("--mode", choices=sorted(DATA_MODES))
    parser.add_argument(
        "--paper", action="store_true", help="explicit read-only paper API collection"
    )
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument(
        "--experiment", type=Path, help="record frozen-candidate shadow observations"
    )
    parser.add_argument(
        "--security-master",
        type=Path,
        help="verified, dated common-stock classification JSON",
    )
    parser.add_argument(
        "--paper-runtime",
        type=Path,
        help="publish received feed freshness to an existing paper runtime",
    )
    parser.add_argument(
        "--summary", action="store_true", help="print the canonical safe summary"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Import an optional bundle and print counts/hash without creating clients."""

    args = _parser().parse_args(argv)
    try:
        from quantpilot.jobs.run_intraday_backtest import external_path

        args.ledger = external_path(args.ledger)
        if args.paper:
            if args.input or args.mode not in {None, "realtime_market_data"}:
                raise ValueError("paper_collection_requires_realtime_mode")
            from quantpilot.paper.intraday.collection import collect_paper

            result = collect_paper(
                args.ledger,
                args.seconds,
                args.experiment,
                args.paper_runtime,
                security_master=args.security_master,
            )
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
        with IntradayData(args.ledger, data_mode=args.mode) as ledger:
            status_code = 0
            if args.input is None:
                result = _summary(
                    ledger,
                    "data_insufficient",
                    [{"scope": "input", "error": "no input bundle supplied"}],
                )
            else:
                try:
                    with args.input.open("r", encoding="utf-8") as stream:
                        payload = json.load(stream)
                    ledger.ingest_bundle(payload)
                    result = _summary(ledger, "imported")
                except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    result = _summary(
                        ledger,
                        "data_insufficient",
                        [{"scope": "input", "error": type(exc).__name__}],
                    )
                    status_code = 2
    except (OSError, TypeError, ValueError) as exc:
        result = {
            "status": "data_insufficient",
            "issues": [{"scope": "input", "error": type(exc).__name__}],
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return status_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["collect_once", "main"]
