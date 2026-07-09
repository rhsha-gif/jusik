# Professional Operator Pure Decision Contracts

These additive contracts define the only surfaces Claude Code may implement for the professional-operator stage.
They are deliberately broker-free: Codex adapters turn the decisions into existing `Signal`, `PortfolioPlan`,
`OrderPlan`, risk-check, state-machine, and audit flows.

## Pullback trend decision

Implementation target: `quantpilot.packages.core.signals.pullback_trend`.

### Input

`PullbackSignalInput` contains:

- immutable strategy id/version and symbol;
- typed daily OHLCV bars plus a `signal_date` cutoff;
- current and maximum policy position weights;
- candidate eligibility and optional exclusion reason;
- the existing calibrated multi-factor score;
- a realtime/reference quote with `quote_as_of` and `evaluated_at` timestamps;
- an explicit data-usability flag.

Bars after `signal_date` must be ignored. At least the configured 120 completed sessions are required. Missing or
unusable data returns a blocked decision rather than inventing values.

### Indicators

`build_pullback_indicators` calculates, as of `signal_date` only:

- close;
- SMA20 and SMA120;
- prior-session RSI14 and current RSI14;
- ATR14 using true range and prior close;
- current volume divided by the mean of the previous 20 completed session volumes.

RSI14 and ATR14 use Wilder smoothing. Seed RSI from the first 14 close-to-close gains/losses (zeroes included
in each 14-period average), seed ATR from the first 14 true ranges, and apply Wilder's recursive update through
the cutoff. `prior_rsi14` is the completed value one session before `rsi14`. Volume ratio excludes the current
session from its 20-session denominator. Numeric outputs are rounded to six decimal places only at the returned
model boundary.

### Decision precedence

`evaluate_pullback_signal` applies this exact order:

1. ineligible candidate, unusable/insufficient data, invalid/future/stale quote -> `blocked`;
2. an existing position with close at or below `SMA20 * 0.94` -> `exit`;
3. an existing position with RSI at least 72 or close at least `SMA20 * 1.20` -> `trim` to 50%;
4. any other existing position -> `hold`;
5. no position and every entry condition below true -> `buy_ready`;
6. uptrend with an incomplete pullback confirmation -> `buy_wait`;
7. otherwise -> `watch`.

Entry requires all of:

- completed close above SMA120;
- prior RSI below 35 and current RSI at least 35;
- volume ratio at least 1.05;
- multi-factor score at least 68;
- quote no more than 0.5% above the completed close;
- quote age from 0 through 30 seconds inclusive.

The returned `PullbackSignalDecision` contains no order shape, approval, broker mode, or submission authority.

## Position protective-risk decision

Implementation target: `quantpilot.packages.core.risk.position_exit`.

`evaluate_position_risk` receives an attributed long position, current quote, ATR14, SMA20, RSI14, and timestamps.
It calculates:

```text
protective_stop = max(average_entry_price * 0.92, average_entry_price - 2 * ATR14)
```

Decision precedence is fixed:

1. quote age outside 0..30 seconds -> `blocked`, zero exit quantity;
2. current price at or below the protective stop -> full `exit`;
3. current price at or below `SMA20 * 0.94` -> full `exit`;
4. RSI at least 72 or price at least `SMA20 * 1.20` -> `trim` 50%;
5. otherwise -> `hold`.

The evaluator never submits or formats an order. Codex alone maps a risk-reducing decision into a limit order and
re-runs the existing risk/state-machine gates.

## Compatibility and safety

- Models inherit `HarnessModel` and reject unknown fields.
- Defaults encode the locked user decisions; changing them is a material policy/contract change.
- Functions are deterministic for identical input and do not read clocks, environment variables, files, networks,
  repositories, brokers, LLMs, or RL outputs.
- Quote timestamps must be timezone-aware. A future quote or either naive timestamp fails closed with an auditable
  blocked reason.
- Existing Level 0-5 APIs remain backward compatible until Codex integration explicitly adds defaulted fields.
- Live trading remains disabled and outside these contracts.
