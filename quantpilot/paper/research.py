"""Persisted, asynchronous strategy research. Candidate code stays inert on the host."""

from __future__ import annotations

import ast
from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256

from quantpilot.paper.calendar import KST
from quantpilot.paper.store import encode


def candidates(store):
    from quantpilot.paper.lab import Candidate

    return [
        Candidate.model_validate_json(row[0])
        for row in store.db.execute("SELECT body FROM lab ORDER BY id")
    ]


def save(store, candidate):
    store.db.execute(
        "INSERT INTO lab VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
        (candidate.candidate_id, candidate.model_dump_json()),
    )


def input_at(store, now):
    """Only completed observed bars; no positions, account identifiers, or file paths."""
    from quantpilot.paper.strategy import validate_bars

    result = {}
    for symbol in store.get("universe", []):
        bars = [
            bar
            for bar in store.load_bars(symbol)
            if bar.start.astimezone(KST).date() == now.astimezone(KST).date()
            and bar.start + timedelta(minutes=1) <= now
        ]
        if not bars or not 0 <= (now - bars[-1].start).total_seconds() <= 150:
            continue
        try:
            validate_bars(bars, now)
        except ValueError:
            continue
        result[symbol] = [
            asdict(bar) | {"start": bar.start.isoformat()} for bar in bars
        ]
    return {"observed_at": now.isoformat(), "bars": result}


def static_gate(source):
    """Conservative pure-function subset; Docker remains the execution boundary."""
    tree = ast.parse(source)
    banned = {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "globals",
        "locals",
        "vars",
        "input",
        "breakpoint",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            raise ValueError("candidate_import_or_global")
        if isinstance(node, ast.Name) and (
            node.id in banned or node.id.startswith("__")
        ):
            raise ValueError("candidate_forbidden_name")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("candidate_private_attribute")
    if not any(
        isinstance(n, ast.FunctionDef) and n.name == "generate_signals"
        for n in tree.body
    ):
        raise ValueError("candidate_function_missing")


def verified_execution(candidate, evidence, now, config, readiness, execute=None):
    from quantpilot.paper.lab import (
        run_signal_code,
        SignalRunResult,
        VerificationEvidence,
    )

    execute = execute or run_signal_code
    static_gate(candidate.source)
    if not evidence["bars"]:
        raise ValueError("candidate_test_data_missing")
    results = [execute(candidate, evidence, config, readiness) for _ in range(2)]
    if (
        any(not isinstance(r, SignalRunResult) for r in results)
        or results[0] != results[1]
    ):
        raise ValueError("candidate_reproducibility_failed")
    seen = set()
    for signal in results[0].suggestions:
        if (
            signal.symbol not in evidence["bars"]
            or signal.strategy_id != candidate.strategy_id
            or signal.symbol in seen
        ):
            raise ValueError("candidate_output_identity")
        seen.add(signal.symbol)
    # A fresh container gets only the past-prefix fixture; the host never exposes future bars.
    for bars in evidence["bars"].values():
        if any(
            datetime.fromisoformat(b["start"]) + timedelta(minutes=1) > now
            for b in bars
        ):
            raise ValueError("candidate_future_input")
    tests = [
        VerificationEvidence(
            kind=kind,
            passed=True,
            source_hash=candidate.source_hash,
            candidate_version=candidate.version,
            observed_at=now,
            verifier="paper_fixed_runner_v1",
            provenance="trusted_test_runner",
        )
        for kind in ("safety", "reproducibility", "no_lookahead")
    ]
    return tests


def postclose(store, now, review, runner=None, execute=None, readiness_check=None):
    """At most one generation attempt per KST date. Failures leave inert candidates."""
    if not store.policy.research_enabled:
        return {"status": "disabled"}
    from quantpilot.paper.lab import (
        SandboxConfig,
        SandboxReadiness,
        verify_sandbox_readiness,
        Candidate,
        LabState,
        LabError,
        IndependentReview,
        ForwardTradeEvidence,
        VerificationEvidence,
        run_candidate_generation,
        run_candidate_review,
        register_generated,
        move_to_shadow,
        evaluate_promotion,
        move_to_paper_trial,
    )
    from quantpilot.paper.intelligence import default_cli_runner

    try:
        config = SandboxConfig(enabled=True, pinned_image=store.policy.sandbox_image)
        readiness = (readiness_check or verify_sandbox_readiness)(config, now)
        if not isinstance(readiness, SandboxReadiness):
            return {"status": "held", "reason": "sandbox_not_ready"}
        state = LabState(candidates=tuple(candidates(store)))
        for candidate in state.candidates:
            if candidate.stage != "shadow":
                continue
            record = store.get("lab_evidence:" + candidate.source_hash)
            if not record:
                continue
            tests = [
                VerificationEvidence.model_validate_json(encode(x))
                for x in record["tests"]
            ]
            independent = IndependentReview.model_validate_json(
                encode(record["review"])
            )
            trades = [
                ForwardTradeEvidence.model_validate_json(encode(x))
                for x in store.get("lab_trades:" + candidate.source_hash, [])
            ]
            decision = evaluate_promotion(candidate, trades, tests, independent, now)
            changed = move_to_paper_trial(state, candidate.candidate_id, decision)
            if isinstance(changed, LabState):
                state = changed
                for updated in changed.candidates:
                    save(store, updated)
                store.audit(
                    "candidate_paper_trial",
                    {"source_hash": candidate.source_hash, "allocation_cap": 0.1},
                    now,
                )
        if sum(c.stage == "shadow" for c in state.candidates) >= 3:
            return {"status": "skipped", "reason": "shadow_slots_full"}
        # Generation needs a recorded qualitative improvement reason, not an arbitrary daily quota.
        risks = review.get("risks", []) if isinstance(review, dict) else []
        if not risks:
            return {"status": "skipped", "reason": "no_improvement_evidence"}
        key = "generation_attempt:" + now.astimezone(KST).date().isoformat()
        with store.transaction():
            if store.get(key):
                return {"status": "skipped", "reason": "daily_generation_limit"}
            store.put(key, True)
        invoke = runner or default_cli_runner
        generation_evidence = {
            "improvement_reasons": risks,
            "contract": "No imports or I/O. generate_signals(evidence) returns a list of symbol,strategy_id,signal(long/flat/exit),confidence,reason. evidence.bars maps symbols to completed OHLCV rows. Entry-only trial uses host ATR stop and 2R target.",
        }
        source = run_candidate_generation(
            generation_evidence, now, store.policy.primary_ai, invoke
        )
        if isinstance(source, LabError):
            source = run_candidate_generation(
                generation_evidence,
                now,
                "codex" if store.policy.primary_ai == "claude" else "claude",
                invoke,
            )
        if isinstance(source, LabError):
            return {"status": "held", "reason": source.code.value}
        candidate = Candidate.create(
            candidate_id=source.source_hash,
            strategy_id="lab_" + source.source_hash[:12],
            version=source.source_hash,
            source=source.source,
            generator_provider=source.provider,
            generated_at=now,
        )
        # The generated identifier must match its output contract; identity is never supplied by a signal.
        candidate = candidate.model_copy(update={"strategy_id": source.strategy_id})
        if source.strategy_id in store.policy.active_strategies or any(
            c.strategy_id == source.strategy_id for c in state.candidates
        ):
            return {"status": "held", "reason": "candidate_strategy_id_conflict"}
        registered = register_generated(state, candidate, now)
        if isinstance(registered, LabError):
            return {"status": "held", "reason": registered.code.value}
        save(store, candidate)
        evidence_time = datetime.fromisoformat(store.get("evidence", {})["observed_at"])
        evidence = input_at(store, evidence_time)
        tests = verified_execution(candidate, evidence, now, config, readiness, execute)
        independent = run_candidate_review(
            candidate,
            {"tests": [t.kind for t in tests]},
            now,
            "codex" if source.provider == "claude" else "claude",
            invoke,
        )
        if isinstance(independent, LabError) or not independent.approved:
            return {
                "status": "held",
                "reason": "independent_review_unavailable_or_failed",
            }
        store.put(
            "lab_evidence:" + candidate.source_hash,
            {
                "tests": [t.model_dump(mode="json") for t in tests],
                "review": independent.model_dump(mode="json"),
            },
        )
        changed = move_to_shadow(registered, candidate.candidate_id)
        if isinstance(changed, LabError):
            return {"status": "held", "reason": changed.code.value}
        for updated in changed.candidates:
            save(store, updated)
        return {"status": "shadow", "source_hash": candidate.source_hash}
    except Exception as exc:
        store.audit("research_held", {"error": type(exc).__name__}, now)
        return {"status": "held", "reason": type(exc).__name__}


def forward_once(store, now, execute=None, readiness_check=None):
    """Independent worker computes shadow fills; it never mutates experiment cash."""
    from quantpilot.paper.lab import (
        SandboxConfig,
        SandboxReadiness,
        verify_sandbox_readiness,
        run_signal_code,
        SignalRunResult,
    )

    if not store.policy.research_enabled:
        return
    active = [c for c in candidates(store) if c.stage in {"shadow", "paper_trial"}]
    if not active:
        return
    bucket = now.replace(second=0, microsecond=0).isoformat()
    if store.get("shadow_bucket") == bucket:
        return
    try:
        config = SandboxConfig(enabled=True, pinned_image=store.policy.sandbox_image)
        readiness = (readiness_check or verify_sandbox_readiness)(config, now)
        if not isinstance(readiness, SandboxReadiness):
            return
        evidence = input_at(store, now)
        if not evidence["bars"]:
            return
        trial_signals = []
        for candidate in active:
            result = (execute or run_signal_code)(
                candidate, evidence, config, readiness
            )
            if not isinstance(result, SignalRunResult):
                continue
            if (
                result.source_hash != candidate.source_hash
                or result.candidate_version != candidate.version
            ):
                continue
            suggestions = {
                s.symbol: s
                for s in result.suggestions
                if s.strategy_id == candidate.strategy_id
                and s.symbol in evidence["bars"]
            }
            if len(suggestions) != len(result.suggestions):
                continue
            state_key = "shadow_positions:" + candidate.source_hash
            positions = store.get(state_key, {})
            trades_key = "lab_trades:" + candidate.source_hash
            trades = store.get(trades_key, [])
            from quantpilot.paper.strategy import _atr, Signal

            for symbol, rows in evidence["bars"].items():
                bar = rows[-1]
                suggestion = suggestions.get(symbol)
                existing = positions.get(symbol)
                if existing and bar["start"] > existing["bar"]:
                    # Only later observations can fill/close the prior signal. Conservative stop-first ambiguity.
                    close = (
                        bar["low"] <= existing["stop"]
                        or bar["high"] >= existing["target"]
                        or (suggestion and suggestion.signal == "exit")
                    )
                    closes = store.get("session_closes")
                    close = close or bool(
                        closes
                        and now
                        >= datetime.fromisoformat(closes)
                        - timedelta(minutes=store.policy.liquidation_minutes)
                    )
                    if close:
                        price = (
                            min(bar["open"], existing["stop"])
                            if bar["low"] <= existing["stop"]
                            else (
                                existing["target"]
                                if bar["high"] >= existing["target"]
                                else bar["close"]
                            )
                        )
                        fees = (
                            (existing["price"] + price) * store.policy.fee_bps / 10000
                            + price * store.policy.sell_tax_bps / 10000
                        )
                        slip = (
                            (existing["price"] + price)
                            * store.policy.slippage_bps
                            / 10000
                        )
                        trades.append(
                            {
                                "trade_id": existing["id"],
                                "session_date": now.astimezone(KST).date().isoformat(),
                                "closed_at": now.isoformat(),
                                "state": "CLOSED",
                                "cost_adjusted_net": float(
                                    price - existing["price"] - fees - slip
                                ),
                                "source_hash": candidate.source_hash,
                                "candidate_version": candidate.version,
                                "provenance": "trusted_external",
                            }
                        )
                        del positions[symbol]
                    continue
                if existing or not suggestion or suggestion.signal != "long":
                    continue
                closes = store.get("session_closes")
                if not closes or now >= datetime.fromisoformat(closes) - timedelta(
                    minutes=store.policy.entry_cutoff_minutes
                ):
                    continue
                bars = [
                    b
                    for b in store.load_bars(symbol)
                    if b.start.isoformat() <= bar["start"]
                ][-20:]
                atr = _atr(bars)
                price = bar["close"]
                if not atr or not 0 < atr < price:
                    continue
                signal = Signal(
                    candidate.strategy_id,
                    symbol,
                    price,
                    price - atr,
                    price + 2 * atr,
                    suggestion.confidence,
                    suggestion.reason,
                    candidate.version,
                    atr,
                )
                if candidate.stage == "paper_trial":
                    trial_signals.append(asdict(signal))
                else:
                    positions[symbol] = {
                        "id": sha256(
                            (candidate.source_hash + symbol + bar["start"]).encode()
                        ).hexdigest(),
                        "price": price,
                        "stop": signal.stop,
                        "target": signal.target,
                        "bar": bar["start"],
                    }
            with store.transaction():
                store.put(state_key, positions)
                store.put(trades_key, trades)
        store.put(
            "trial_signals", {"observed_at": now.isoformat(), "signals": trial_signals}
        )
        store.put("shadow_bucket", bucket)
    except Exception as exc:
        store.audit("shadow_unavailable", {"error": type(exc).__name__}, now)
