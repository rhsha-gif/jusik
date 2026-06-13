# /review-level-impl

Review a completed QuantPilot implementation level for safety, test coverage, and operational clarity.

## Usage

```
/review-level-impl [level] [--files "path1 path2 ..."]
```

- `level`: one of `3`, `4`, `5` (required)
- `--files`: optional space-separated list of changed files to focus on; defaults to full diff against `main`

## What This Command Does

Invokes three specialist agents in sequence. Any `reject` verdict stops the chain and blocks sign-off.

1. **Risk gate audit** — `risk-gate-auditor` checks that:
   - Live trading defaults remain disabled (`LIVE_TRADING_ENABLED=false`, `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`)
   - `BROKER_MODE` stays `mock` or paper-safe in tests
   - Level authority chains (`authorize_level3/4/5`) cannot be bypassed
   - No LLM/RL output reaches a raw broker order
   - Market orders stay blocked unless the explicit flag is on
   - Reports concrete file paths and line numbers only; no broad refactors proposed.

2. **Test audit** — `test-auditor` checks that:
   - All new authority checks are covered by at least one negative test (flag-disabled blocks)
   - Strategy registry eligibility is deterministically tested
   - Fallback reasons map to documented matrix rows
   - Policy version drift is covered
   - Operator reports are tested for decision fields, risk check fields, and safety flag fields
   - No test requires secrets or live broker access

3. **Runbook review** — `operator-runbook-reviewer` checks that:
   - Operational docs explain disabled defaults in plain language
   - Run-once API instructions are correct and current
   - Blocked-state and fallback explanations are present
   - No doc promises profits or implies live-trading readiness
   - Smoke check command output matches expected blocked state

## Invocation Instructions

When this command is invoked:

1. If no level is provided, ask: "Which implementation level should I review? (3, 4, or 5)"
2. Determine changed files: use `--files` argument if provided; otherwise run `git diff --name-only main` to collect changed files.
3. Read the relevant spec:
   - Level 3/4: `docs/level_3_4_implementation_report.md`
   - Level 5: `docs/level_5_operator_completion_report.md`
4. Invoke `risk-gate-auditor` — if verdict is `reject`, report findings and stop.
5. Invoke `test-auditor` — if verdict is `reject`, report findings and stop.
6. Invoke `operator-runbook-reviewer`.
7. Produce a combined review summary:
   - Overall verdict: `pass` (all three clear) | `revise` (warnings only) | `reject` (any hard block)
   - All findings with file paths and line numbers
   - Remediation steps grouped by agent
8. Write review report to `docs/level_<N>_review_<YYYY-MM-DD>.md`.

## Safety

- No broker API calls
- No executable orders
- No live trading code
- No secrets access

## Output

File: `docs/level_<N>_review_<YYYY-MM-DD>.md`  
Verdict: `pass` | `revise` | `reject`
