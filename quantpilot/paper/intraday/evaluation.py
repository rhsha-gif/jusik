"""Append-only search evidence, sealed selection and conservative admission gates."""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import math
import random
import sqlite3
from pathlib import Path

from quantpilot.paper.calendar import Calendar, KST
from quantpilot.paper.config import aware
from quantpilot.paper.intraday.deployment import digest, VALIDATED_POLICY
from quantpilot.paper.intraday.strategy import SPECS

MIN_TRAIN, MIN_SELECTION, MIN_TEST, MIN_TRADES, MIN_SHADOW = 90, 30, 60, 100, 20


def encoded(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def stamp(value):
    return aware(datetime.fromisoformat(value)).astimezone(timezone.utc)


class Experiment:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            """
        CREATE TABLE IF NOT EXISTS evidence(kind TEXT NOT NULL, key TEXT NOT NULL,
          at TEXT NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(kind,key));
        CREATE TABLE IF NOT EXISTS looks(id INTEGER PRIMARY KEY, strategy TEXT NOT NULL,
          at TEXT NOT NULL, body TEXT NOT NULL);
        """
        )

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            yield
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def append(self, kind, key, value, now):
        body, checksum = encoded(value), digest(value)
        aware(now)
        with self.transaction():
            row = self.db.execute(
                "SELECT body FROM evidence WHERE kind=? AND key=?", (kind, key)
            ).fetchone()
            if row and row[0] != body:
                raise ValueError("immutable_experiment_evidence")
            self.db.execute(
                "INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)",
                (kind, key, now.isoformat(), body, checksum),
            )

    def read(self, kind, key):
        row = self.db.execute(
            "SELECT body,hash FROM evidence WHERE kind=? AND key=?", (kind, key)
        ).fetchone()
        if not row:
            return None
        value = json.loads(row[0])
        if digest(value) != row[1]:
            raise ValueError("experiment_evidence_corrupt")
        return value

    def records(self, kind):
        return [
            self.read(kind, r[0])
            for r in self.db.execute(
                "SELECT key FROM evidence WHERE kind=? ORDER BY key", (kind,)
            ).fetchall()
        ]


def trading_days(dataset, calendar=None):
    dates = sorted(
        {stamp(b["start"]).astimezone(KST).date() for b in dataset.get("bars", [])}
    )
    if not dates:
        return []
    calendar = calendar or Calendar()
    day, end = dates[0], dates[-1]
    result = []
    while day <= end:
        at = datetime.combine(day, datetime.min.time(), KST) + timedelta(hours=12)
        if calendar.session(at):
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def family_manifest(dataset, days):
    return {
        "schema_version": 1,
        "specs": [asdict(s) | {"version": s.version} for s in SPECS],
        "dataset_hash_at_registration": dataset["sha256"],
        "data_mode": dataset["data_mode"],
        "train": days[:MIN_TRAIN],
        "selection": days[MIN_TRAIN : MIN_TRAIN + MIN_SELECTION],
        "test_start": days[MIN_TRAIN + MIN_SELECTION],
        "training_selection_hash": history_hash(
            dataset, days[MIN_TRAIN + MIN_SELECTION]
        ),
        "fee_bps": 1.40527,
        "sell_tax_bps": 20,
        "slippage_bps": [5, 10],
        "alpha": 0.05,
        "execution_policy": VALIDATED_POLICY,
        "selection_rule": "maximum selection net PnL within each hypothesis; version breaks exact ties",
        "uncertainty": "centered trading-day block bootstrap; Bonferroni family and alpha spending 1/(look*(look+1))",
    }


def history_hash(dataset, test_start):
    bars = [
        r
        for r in dataset["bars"]
        if stamp(r["start"]).astimezone(KST).date().isoformat() < test_start
    ]
    universes = [
        r
        for r in dataset["universes"]
        if stamp(r["at"]).astimezone(KST).date().isoformat() < test_start
    ]
    sources = {r["source"] for r in bars + universes}
    return digest(
        {
            "bars": bars,
            "universes": universes,
            "provenance": {s: dataset["provenance"].get(s) for s in sorted(sources)},
        }
    )


def run_study(dataset, experiment, now=None, calendar=None):
    """Training and selection precede a durable freeze; only then is holdout replayed."""
    from quantpilot.paper.intraday.replay import replay, calibrate

    now = now or datetime.now(timezone.utc)
    days = trading_days(dataset, calendar)
    if len(days) < MIN_TRAIN + MIN_SELECTION + MIN_TEST:
        return {
            "status": "data_insufficient",
            "trading_days": len(days),
            "required_days": 180,
            "dataset_hash": dataset.get("sha256"),
            "reasons": ["minimum_calendar_coverage"],
        }
    manifest = experiment.read("family", "intraday2")
    if manifest is None:
        manifest = family_manifest(dataset, days)
        experiment.append("family", "intraday2", manifest, now)
    if manifest["specs"] != family_manifest(dataset, days)["specs"]:
        raise ValueError("strategy_family_changed_requires_new_experiment")
    if not set(manifest["train"] + manifest["selection"]).issubset(days):
        raise ValueError("registered_training_dates_missing")
    if (
        history_hash(dataset, manifest["test_start"])
        != manifest["training_selection_hash"]
    ):
        raise ValueError("registered_training_or_selection_data_changed")
    frozen = experiment.records("frozen")
    if not frozen:
        trial_results = []
        for spec in SPECS:
            training = replay(
                dataset,
                spec,
                days=manifest["train"],
                slippage_bps=10,
                calendar=calendar,
            )
            calibration = calibrate(training)
            selection = replay(
                dataset,
                spec,
                days=manifest["selection"],
                slippage_bps=10,
                calibration=calibration,
                calendar=calendar,
            )
            trial = {
                "version": spec.version,
                "strategy_id": spec.strategy_id,
                "training": training,
                "selection": selection,
                "calibration": calibration,
            }
            experiment.append("trial", spec.version, trial, now)
            trial_results.append(trial)
        for name in sorted({s.strategy_id for s in SPECS}):
            selected = sorted(
                (r for r in trial_results if r["strategy_id"] == name),
                key=lambda r: (-r["selection"]["net_pnl"], r["version"]),
            )[0]
            candidate = {
                "strategy_id": name,
                "version": selected["version"],
                "calibration": selected["calibration"],
                "family_hash": digest(manifest),
                "frozen_at": now.isoformat(),
                "training_hash": digest(selected["training"]),
                "selection_hash": digest(selected["selection"]),
            }
            experiment.append("frozen", name, candidate, now)
        frozen = experiment.records("frozen")
    outputs = []
    test_days = [d for d in days if d >= manifest["test_start"]]
    for candidate in frozen:
        spec = next(s for s in SPECS if s.version == candidate["version"])
        key = candidate["strategy_id"] + ":" + dataset["sha256"]
        result = experiment.read("holdout", key)
        if result is None:
            scenarios = {}
            for slip in (5, 10):
                scenarios[str(slip)] = replay(
                    dataset,
                    spec,
                    days=test_days,
                    slippage_bps=slip,
                    calibration=candidate["calibration"],
                    calendar=calendar,
                )
            sensitivity = {}
            for name, kwargs in {
                "delay_2m": {"latency_minutes": 2},
                "half_fills": {"fill_fraction": 0.5},
                "no_fills": {"fill_fraction": 0},
                "gap_50bps": {"adverse_gap_bps": 50},
            }.items():
                sensitivity[name] = replay(
                    dataset,
                    spec,
                    days=test_days,
                    slippage_bps=10,
                    calibration=candidate["calibration"],
                    calendar=calendar,
                    **kwargs
                )
            result = {
                **candidate,
                "dataset_hash": dataset["sha256"],
                "data_mode": dataset["data_mode"],
                "provenance": dataset.get("provenance", {}),
                "scenarios": scenarios,
                "sensitivity": sensitivity,
                "test_days": test_days,
            }
            experiment.append("holdout", key, result, now)
        outputs.append(result)
    return {
        "status": "evaluated",
        "family_hash": digest(manifest),
        "dataset_hash": dataset["sha256"],
        "candidates": outputs,
    }


def day_block_test(values, alpha, seed=20260911, draws=9999):
    """Resample whole days, including zero-trade days. Minimum p is 1/(draws+1)."""
    if not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("bootstrap_alpha_invalid")
    if len(values) < MIN_TEST or not all(math.isfinite(x) for x in values):
        return {"p_value": 1.0, "lower_mean": None, "passes": False}
    mean = sum(values) / len(values)
    if mean <= 0:
        return {"p_value": 1.0, "lower_mean": mean, "passes": False}
    # Alpha spending must not make passing numerically impossible after a few looks.
    draws = max(draws, math.ceil(1 / alpha))
    rng = random.Random(seed)
    # Five adjacent trading days per block preserve short serial dependence.
    n, block = len(values), 5
    centered = [v - mean for v in values]
    null_means = []
    for _ in range(draws):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(centered[(start + i) % n] for i in range(block))
        null_means.append(sum(sample[:n]) / n)
    p = (1 + sum(v >= mean for v in null_means)) / (draws + 1)
    null_means.sort()
    lower = mean - null_means[min(draws - 1, math.ceil((1 - alpha) * draws) - 1)]
    return {
        "p_value": p,
        "lower_mean": lower,
        "passes": p <= alpha and lower > 0,
        "method": "centered circular 5-trading-day block bootstrap",
        "draws": draws,
    }


def evaluate_candidate(experiment, strategy_id, dataset_hash, now=None):
    now = now or datetime.now(timezone.utc)
    with experiment.transaction():
        manifest = experiment.read("family", "intraday2")
        candidate = experiment.read("frozen", strategy_id)
        result = experiment.read("holdout", strategy_id + ":" + dataset_hash)
        reasons, tests = [], {}
        look = experiment.db.execute("SELECT COUNT(*) FROM looks").fetchone()[0] + 1
        family_size = len(SPECS)
        alpha = 0.05 / (look * (look + 1) * family_size * 2)
        complete = manifest is not None and candidate is not None and result is not None
        if not complete:
            reasons.append("missing_frozen_experiment_history")
        elif manifest.get("specs") != [
            asdict(s) | {"version": s.version} for s in SPECS
        ] or {r.get("version") for r in experiment.records("trial")} != {
            s.version for s in SPECS
        }:
            reasons.append("incomplete_search_history")
        else:
            if (
                candidate.get("family_hash") != digest(manifest)
                or result.get("family_hash") != digest(manifest)
                or result.get("version") != candidate["version"]
            ):
                reasons.append("frozen_evidence_identity_mismatch")
            for trial in experiment.records("trial"):
                for phase in ("training", "selection"):
                    if trial[phase].get("quality_issues") or trial[phase].get(
                        "unresolved"
                    ):
                        reasons.append("unresolved_data_in_" + phase)
            for slip in ("5", "10"):
                replay_result = result["scenarios"][slip]
                if (
                    replay_result.get("dataset_hash") != dataset_hash
                    or replay_result.get("version") != candidate["version"]
                ):
                    reasons.append("replay_identity_mismatch:" + slip)
                if (
                    len(replay_result["trading_days"]) < MIN_TEST
                    or replay_result["round_trips"] < MIN_TRADES
                ):
                    reasons.append("minimum_out_of_sample_evidence:" + slip)
                if replay_result["unresolved"] or replay_result["quality_issues"]:
                    reasons.append("unresolved_data_or_positions:" + slip)
                if replay_result["net_pnl"] <= 0:
                    reasons.append("nonpositive_net_profit:" + slip)
                tests[slip] = day_block_test(
                    [
                        replay_result["daily_pnl"].get(d, 0)
                        for d in replay_result["trading_days"]
                    ],
                    alpha,
                )
                if not tests[slip]["passes"]:
                    reasons.append("statistical_uncertainty:" + slip)
            if result["data_mode"] == "fixture":
                reasons.append("fixture_is_not_market_evidence")
        historical_passed = not reasons
        shadow_days = []
        shadow_round_trips = 0
        if candidate:
            for r in experiment.records("shadow"):
                if (
                    r.get("version") == candidate["version"]
                    and r.get("candidate_hash") == digest(candidate)
                    and stamp(r["started_at"]) > stamp(candidate["frozen_at"])
                    and r.get("data_mode") == "realtime_market_data"
                    and r.get("completed") is True
                    and r.get("signal_mismatches") == 0
                    and r.get("quality_issues") == []
                    and r.get("quote_observations", 0) > 0
                    and r.get("tick_observations", 0) > 0
                    and r.get("execution_comparisons", 0) > 0
                    and r.get("orders_submitted") == 0
                    and r.get("paired_rules_ai") is not None
                ):
                    shadow_days.append(r["day"])
                    shadow_round_trips += sum(
                        t.get("strategy_id") == strategy_id
                        for t in r["paired_rules_ai"]["rules"].get("trades", [])
                    )
        if len(set(shadow_days)) < MIN_SHADOW:
            reasons.append("minimum_20_realtime_shadow_days")
        if shadow_round_trips == 0:
            reasons.append("shadow_completed_execution_evidence_missing")
        data_short = any(
            r.startswith(("minimum_out_of_sample", "unresolved_data", "missing_frozen"))
            for r in reasons
        )
        status = (
            "paper_eligible"
            if not reasons
            else "data_insufficient" if data_short else "evidence_insufficient"
        )
        report = {
            "status": status,
            "strategy_id": strategy_id,
            "version": candidate["version"] if candidate else None,
            "dataset_hash": dataset_hash,
            "family_hash": digest(manifest) if manifest else None,
            "candidate_hash": digest(candidate) if candidate else None,
            "look": look,
            "alpha_family": 0.05,
            "alpha_per_test_this_look": alpha,
            "multiple_comparison_corrected": complete
            and "incomplete_search_history" not in reasons
            and "frozen_evidence_identity_mismatch" not in reasons,
            "historical_passed": historical_passed,
            "tests": tests,
            "shadow_days": len(set(shadow_days)),
            "shadow_round_trips": shadow_round_trips,
            "reasons": sorted(set(reasons)),
            "evaluated_at": now.isoformat(),
            "orders_armed": False,
            "execution_policy": dict(VALIDATED_POLICY),
            "sensitivity": {
                name: {
                    key: scenario.get(key)
                    for key in (
                        "net_pnl",
                        "round_trips",
                        "unresolved",
                        "quality_issues",
                        "assumptions",
                    )
                }
                for name, scenario in (result or {}).get("sensitivity", {}).items()
            },
        }
        experiment.db.execute(
            "INSERT INTO looks(strategy,at,body) VALUES(?,?,?)",
            (strategy_id, now.isoformat(), encoded(report)),
        )
        return report
