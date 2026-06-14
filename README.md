# QuantPilot Operator Pre-Harness

Fixture-only operating harness for QuantPilot Operator. Live broker trading is not implemented or enabled.

## Current Buy/Sell Timing Flow

This section describes the current software behavior. It is not investment advice,
and it does not enable live trading.

When your intent is represented as a policy, QuantPilot stores it in `UserPolicy`.
The policy controls limits such as max position weight, max sector weight,
minimum cash, single-order cash limit, rebalance frequency, allowed order types,
execution mode, broker mode, preferred themes, preferred sectors, and blocklists.
`compile_policy_text` preserves the existing typed `PolicyAST` compatibility
surface. Callers that need downstream typed constraints can opt into
`compile_semantic_policy_text`, which returns a `PolicyCompilationResult` with
`SemanticPolicy`, `UniverseConstraints`, `ForbiddenConstraints`, and
`RiskBudget`. Ambiguous, short, inverse, live-trading, automation-without-review,
and market-order intents fail closed and are not orderable.

Current risk defaults:

- Moderate: max position 15%, minimum cash 20%, max sector 40%.
- Conservative: max position 10%, minimum cash 30%.
- Aggressive: max position 20%, minimum cash 10%.
- Single order cash is capped at 1,000,000 KRW by default.
- Limit orders are the default and market orders are disabled.

The current timing pipeline is deterministic:

1. Policy parsing: natural-language text is parsed into `UserPolicy`.
2. Candidate universe: the local universe is filtered by blocklist, preferred
   themes, liquidity, fixture halts, and data readiness.
3. Signal generation: the strategy creates one of `buy_ready`, `buy_wait`,
   `hold`, `trim`, `exit`, `watch`, or `blocked`.
4. Portfolio planning: signals become target weights and limit-order intents.
5. Proposal generation: intents are sorted by largest weight delta, risk checked,
   assigned deterministic idempotency keys, and turned into reviewable proposals.
6. Submission: approval-required mode needs explicit user approval and then a
   fresh risk check before mock or paper broker submission.

Current buy/hold/sell signal rules:

- `buy_ready`: no current position and the latest bar/indicator shows trend and
  pullback recovery. In the fixture path this means `close > ma20`, `rsi <= 35`,
  and `volume_ratio >= 1.1`. In the Level 1-2 indicator path this means
  `technical_score >= 68`, `volume_ratio >= 1.05`, and `rsi <= 65`.
- `buy_wait`: setup is forming but not ready.
- `hold`: an existing position remains inside the risk band.
- `trim`: an existing position is overheated, or above the policy position cap.
- `exit`: an existing position breaks the risk moving-average rule.
- `blocked`: the candidate is blocked by policy, fixture halt, liquidity, data,
  or theme mismatch.

Sizing is target-weight based, not all-in/all-out. A `buy_ready` signal targets
up to `strength * max_position_weight`, capped by the policy. `trim` targets
about half the current weight. `exit` and `blocked` target zero. Order notional
is capped by the target delta, single-order cash limit, available cash above the
minimum cash reserve, and position/sector limits.

Order timing is also safety-gated. Before any proposal or submission, the risk
gate checks kill switch state, policy version, broker mode, cash, minimum cash,
position and sector exposure, single-order limit, daily order count, daily
turnover, order type, duplicate idempotency keys, quote age, conflicting unfilled
orders, and daily/monthly loss stops. Submission reruns a fresh risk check.

Important current limitations:

- `DATA_MODE=fixture` remains the default.
- Live broker trading is disabled.
- The current harness is long-only target-weight based. A bearish or short
  direction is not a separate executable policy input yet.
- Preferred sectors are parsed into the policy, but current candidate filtering
  uses preferred themes, not preferred sectors. Sector is still enforced later as
  a max sector exposure risk check.
- Stop and take-profit values are currently signal/proposal hints, not live stop
  orders.
- Level 4 guarded autopilot and Level 5 fully automated operator paths reuse the
  same proposal logic, but they are disabled by default and add stricter authority
  checks before any mock or paper submission.

## Safe Defaults

```text
LIVE_TRADING_ENABLED=false
GUARDED_AUTOPILOT_ENABLED=false
FULLY_AUTOMATED_OPERATOR_ENABLED=false
BROKER_MODE=mock
DEFAULT_ORDER_TYPE=limit
MARKET_ORDERS_ENABLED=false
DATA_MODE=fixture
```

## Commands

`make` is not available in the verified Windows environment, so use these equivalents:

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
python -m uvicorn quantpilot.services.api.main:app --reload
```

When pytest temporary-directory permissions fail on Windows, use the same
workspace-local temp directory used by the hardening checks:

```powershell
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp
```

Compatible systems can also use:

```powershell
make test
make smoke
make api
```
