# Fable5 Recipe: QuantPilot Level 3-4 Autopilot

**Recipe ID:** `qp-l34-autopilot-2026-06-12-v1`
**Author:** Claude Fable 5 (Quant Recipe Architect)
**Status:** Design artifact. No runtime authority. Codex implements; user approves promotion.
**Safety assertions:** `live_trading_enabled: false` · `broker_mode: mock | paper` · `rl_output_type: target_weight_delta | strategy_selection` · `fallback_level: 2`

---

## 1. Outcome Summary

QuantPilot Level 3-4 should be implemented as a deterministic, rule-table-driven pipeline first, with every advanced technique (optimization, RL, HRP, Black-Litterman) locked behind explicit research gates. The recommended Level 3 design converts already-built Level 1-2 signals (`Signal` with `target_weight_hint`, `stop_price_hint`, `take_profit_hint`) into limit-only order proposals through a fixed algorithm: compute weight delta against the current `PortfolioSnapshot`, cap by policy limits, price at the last traded price rounded to the KRX tick (never chasing upward), attach a deterministic idempotency key, and present the proposal with a full explanation block for the user to approve, modify, or reject. No proposal can reach `submitted` status without passing the `OrderStatus` state machine in `quantpilot/packages/core/execution/state_machine.py` and a fresh `RiskCheck`.

Level 4 reuses the identical proposal algorithm — there is no separate "autopilot order generator." The only difference is who approves: at Level 4 a deterministic authority-check sequence replaces per-order human approval, and that sequence is strictly more restrictive than Level 3, not less. Every automatic submission must pass, in order: kill-switch check, policy-version consistency, broker health, quote freshness, trading-session window, full risk limit matrix (Section 6), daily order count, daily turnover, monthly loss state, and duplicate/idempotency check. Any single failure blocks the order; repeated or severe failures demote the system to Level 3 or Level 2 automatically and log the demotion.

The risk limit matrix (Section 6) is the contract between this recipe and the Codex risk gate in `quantpilot/packages/core/risk/gatekeeper.py`. It uses the user baseline guardrails (single order ≤ 1,000,000 KRW, max position weight 0.15, max sector weight 0.40, max 3 orders/day, daily turnover ≤ 0.20, min cash 0.20, monthly loss pause at -5%, monthly loss stop at -10%) and adds the operational limits the baseline leaves open: stale quotes older than 30 seconds block automatic submission, limit orders expire after 30 minutes, and one re-proposal per symbol per day is the maximum permitted "second attempt."

Strategy logic is externalized into a versioned YAML schema (Section 7) validated by `quantpilot/packages/core/strategies/loader.py`. A strategy file declares its signals, entry/exit rules, sizing method, allowed execution levels, and its validation record. A strategy that has not passed the backtest and walk-forward protocol in Section 8 cannot declare `allowed_execution_levels: [3]`, and one that has not additionally completed the paper-trading and Level 3 observation periods cannot declare level 4. Promotion status lives in the YAML and in the database, and the loader must refuse to run a strategy at a level it has not earned.

Portfolio sizing starts with capped score weighting — proportional to quant score, capped at policy limits, residual to cash. This is the only method Codex implements for MVP. Inverse-volatility weighting is the first permitted upgrade after it passes the same validation protocol. Risk parity, HRP, and Black-Litterman are research-only: they are documented in Section 9 with their failure modes, but Codex must not wire them into the live path until each passes the promotion ladder. This ordering follows the estimation-error literature: simpler weighting schemes degrade more gracefully when return and covariance estimates are noisy.

RL is a bounded research pathway, not an execution engine. The RL policy observes market, portfolio, and risk state; it may output only `target_weight_delta` (bounded, discrete grid) or `strategy_selection` (choice among already-approved strategies). Both outputs map to `Signal.target_weight_hint` and then flow through the same planner, risk gate, and permission ladder as any deterministic signal. RL can never call a broker, never emits order fields, and always has a deterministic fallback strategy. Its promotion ladder (offline backtest → walk-forward → paper → Level 3 observation → Level 4 with halved caps) is longer than the deterministic one on purpose.

Backtest claims are treated as overfitting-prone until proven otherwise. Section 8 mandates next-bar execution, explicit transaction costs and slippage, liquidity filters, walk-forward windows, parameter sensitivity, regime breakdown, a Deflated-Sharpe-style sanity check, and a PBO-style warning before any strategy reaches Level 3 — and a minimum paper-trading period plus live Level 3 observation before Level 4. Backtests are not evidence of future profits; they are evidence that a strategy is not obviously broken.

Codex should implement in the order given in Section 14: Level 3 proposal/approval flow first, then the risk matrix extensions, then Level 4 guarded execution against MockBroker/PaperBroker only, then validation reports, then (and only then) real broker adapters, with RL research mode last. Live trading remains disabled by default at every stage (`OperationReport.live_trading_enabled = False`).

---

## 2. Grounding Extract

**Project constraints used**

- Existing schemas in `quantpilot/packages/core/schemas.py` are the source of truth: `ExecutionMode` (line 19), `OrderStatus` state machine values (line 37), `BrokerMode` (line 51), `UserPolicy` (line 72), `Signal.target_weight_hint` (line 147), `RiskCheck.expires_at` default of 10 minutes (line 297), `OrderPlan.idempotency_key` required (line 306), `OperationReport.live_trading_enabled = False` (line 358).
- `UserPolicy` already carries: `max_position_weight = 0.15`, `max_sector_weight = 0.40`, `min_cash_weight = 0.20`, `daily_loss_limit = -0.03`, `monthly_loss_limit = -0.05`, `single_order_cash_limit = 1,000,000`, `allowed_order_types = [limit]`, `min_avg_daily_value = 5,000,000`, `broker = mock`.
- Fields **not** yet in `UserPolicy` and therefore schema extensions for Codex: `max_daily_orders`, `max_daily_turnover`, `monthly_loss_stop`, `stale_quote_max_age_seconds`, `order_expiry_minutes`, `kill_switch_engaged`, `authority_level`.
- Pre-harness components already exist: `risk/gatekeeper.py`, `execution/state_machine.py`, `brokers/mock_broker.py`, `brokers/paper_broker.py`, `db/audit.py`, `strategies/loader.py`, `rl/outputs.py`, smoke test `jobs/run_smoke.py`.
- An example strategy spec exists at `quantpilot/docs/strategy_specs/pullback_trend_v1.yaml`; Section 7 extends it without breaking its existing fields.

**Safety constraints used**

- No LLM places broker orders; Fable5 outputs are specifications only.
- RL output restricted to `target_weight_delta` or `strategy_selection`, mapped to `Signal.target_weight_hint`.
- Every Level 3 order requires risk check + user approval; every Level 4 order requires risk check + policy authority + stale-data + kill-switch checks.
- Any failed or uncertain condition demotes to a safer mode; live trading disabled by default.

**Quant research ingredients used** (per `docs/claude/05_REFERENCE_BASIS.md`; verify URLs before production use)

- Walk-forward validation: Pardo (2008), *The Evaluation and Optimization of Trading Strategies* — ≥ 5 windows, in-sample ≥ 3× out-of-sample. `[PENDING VERIFICATION]`
- Deflated Sharpe Ratio: Bailey & López de Prado (2014), *JPM* 40(5). `[PENDING VERIFICATION]`
- Probability of Backtest Overfitting: Bailey, Borwein, López de Prado & Zhu (2016), *J. Computational Finance* 20(4); PBO < 0.5 minimum. `[PENDING VERIFICATION]`
- HRP: De Prado (2016), *JPM* 42(4). Black-Litterman: Black & Litterman (1990). Markowitz (1952), *JF* 7(1). `[PENDING VERIFICATION]`
- Trend/momentum basis for the example strategy: Moskowitz, Ooi & Pedersen (2012), *JFE* 104(2); Jegadeesh & Titman (1993), *JF* 48(1). `[PENDING VERIFICATION]`
- Framework patterns (design inspiration, not dependencies): Qlib (arXiv:2009.11189), vectorbt, Backtrader, LEAN, NautilusTrader, PyPortfolioOpt, FinRL (arXiv:2011.09607). `[PENDING VERIFICATION]`

**Assumptions made**

- Market is KRX cash equities (`UserPolicy.market = "KR_STOCK"`), KRW account, no leverage, no shorting, no derivatives.
- Quote feed delivers last price, bid/ask if available, and a `quote_time` timestamp; daily OHLCV history is available per `tests/fixtures/ohlcv.json`.
- Strategy timeframe is daily; intraday strategies are out of scope for this recipe.
- Korean retail tax/fees are modeled as a flat per-side cost in backtests (Section 8) rather than exact statutory schedules; exact rates are a data dependency for Codex.

**Open questions (non-blocking)**

- Exact KRX tick-size table version to encode (Codex should source the current table; the recipe only requires "round to valid tick").
- Whether the production quote provider exposes bid/ask or last-only (the limit price logic below works with last-only).
- Sector classification source (GICS vs. KRX industry codes) for `max_sector_weight` — any consistent single source is acceptable.

---

## 3. Permission Ladder

Authority is a single integer field `authority_level ∈ {1,2,3,4}` stored per policy, changed only by the transitions below. Every transition writes an `AuditLogEvent` and requires the listed confirmation.

### Level 2 → Level 3 (enable order proposals)

| Item | Specification |
|---|---|
| Preconditions | ≥ 1 strategy YAML with `promotion_status: validated_l3` (passed Section 8 Level 3 gates); pre-harness smoke test green; `BrokerMode ∈ {mock, paper}` |
| Promotion criteria | Strategy: OOS Sharpe > 0, PBO < 0.5, walk-forward windows ≥ 5 all complete; ≥ 20 trading days of Level 2 signal history recorded with no schema errors |
| Demotion triggers | n/a (promotion) |
| User confirmation | Explicit opt-in screen; user re-enters policy limits; confirmation persisted with `policy_version` |
| Audit event | `authority_promoted_l2_to_l3` |
| Codex test | `test_level2_to_level3_promotion_gate` |

### Level 3 → Level 4 (enable guarded auto-submission)

| Item | Specification |
|---|---|
| Preconditions | ≥ 60 trading days operated at Level 3; ≥ 30 proposals decided by user; kill switch implemented and test-fired within last 30 days; strategy `promotion_status: validated_l4` |
| Promotion criteria | Proposal modification+rejection rate < 20% over last 30 decisions; zero risk-gate bypass incidents; realized portfolio drawdown during L3 period within `max_portfolio_drawdown_pct`; paper-trading report (Section 8) signed off |
| Demotion triggers | n/a (promotion) |
| User confirmation | Two-step: (1) user reviews and re-confirms every guardrail value in Section 6, (2) types an explicit confirmation phrase; both logged |
| Audit event | `authority_promoted_l3_to_l4` |
| Codex test | `test_level3_to_level4_promotion_gate` |

### Level 4 → Level 3 (fallback to approval mode)

| Item | Specification |
|---|---|
| Demotion triggers (any one) | `daily_loss_limit` breached; 3 consecutive automatic orders blocked by the risk gate in one session; broker health check fails twice in 10 minutes; stale-quote block rate > 50% over 10 attempts; `policy_version_mismatch` detected; unfilled-order re-proposal also expires |
| Behavior | Pending automatic submissions are cancelled; open proposals convert to Level 3 approval items; no positions are force-closed |
| User confirmation | None required to demote (demotion is automatic and immediate); user must re-promote per the L3→L4 row |
| Audit event | `authority_demoted_l4_to_l3` with `trigger` field |
| Codex test | `test_level4_demotes_to_level3_on_trigger` |

### Level 4 → Level 2 (fallback to suggestions only)

| Item | Specification |
|---|---|
| Demotion triggers (any one) | `monthly_loss_pause_new_buys` (-0.05) reached → new-buy proposals disabled, sells remain at L3; strategy `promotion_status` revoked; schema validation failure in any strategy YAML in the active set |
| Behavior | System emits signals and rebalance suggestions only (`order_submission_enabled = False` as in `RebalanceSuggestionReport`) |
| User confirmation | Not required to demote; required to restore |
| Audit event | `authority_demoted_to_l2` with `trigger` field |
| Codex test | `test_demotion_to_level2_disables_order_creation` |

### Level 4 → Full stop / kill switch

| Item | Specification |
|---|---|
| Triggers | User presses kill switch (one click, no confirmation dialog); `monthly_loss_stop_all_autotrading` (-0.10) reached; audit-log write failure (if events cannot be recorded, trading must not continue) |
| Behavior | All unsubmitted plans → `cancelled`; open broker orders → cancel requested; `authority_level` → 2; `kill_switch_engaged = True`; no new proposals of any kind until released |
| Release | User-only, with the same two-step confirmation as L3→L4; release does not restore Level 4 automatically — system resumes at Level 2 |
| Audit events | `kill_switch_engaged`, `kill_switch_released` |
| Codex tests | `test_kill_switch_halts_all_paths`, `test_kill_switch_release_resumes_at_level2` |

---

## 4. Level 3 Order Proposal Recipe

### Input contract

- `UserPolicy` (current `policy_version`)
- `PortfolioSnapshot` (≤ 5 minutes old; otherwise refresh before planning)
- `Signal[]` for the trading date, each with `action`, `strength`, `target_weight_hint`, `reason`, `reason_codes`, `policy_version`
- `PortfolioPlan` from the planner (`target_weights`, `cash_target_weight`, `order_intents`)
- Quote per symbol: `{symbol, last_price, quote_time}` (bid/ask optional)

### Output contract

`OrderPlan` records with `status = proposed`, each containing an `OrderIntent` (limit-only), `idempotency_key`, `risk_check_id` referencing a passed `RiskCheck`, plus an explanation block (fields below) persisted alongside the plan.

### Deterministic proposal algorithm

```text
for each symbol in plan.target_weights, ordered by |weight_delta| descending:
    weight_delta = target_weight - current_weight(snapshot, symbol)
    if |weight_delta| < min_rebalance_band (default 0.01): skip          # no churn
    side = buy if weight_delta > 0 else sell
    if side == buy and new_buys_paused(): skip with reason_code "loss_pause"
    notional = min(|weight_delta| * snapshot.equity,
                   policy.single_order_cash_limit,
                   remaining_cash_budget if side == buy else position_value)
    limit_price = tick_round(last_price, direction = down if buy else up)
    quantity = floor(notional / limit_price); if quantity == 0: skip
    intent = OrderIntent(limit-only, quantity, limit_price, notional, target_weight, reason)
    key = idempotency_key(policy, strategy, symbol, side, trading_date)
    if key already exists today: skip with reason_code "duplicate"
    plan = OrderPlan(draft) -> run risk gate -> risk_checked -> proposed
    emit audit "proposal_created"
stop when daily proposal count reaches policy.max_daily_orders
```

### Position sizing

Delta-to-target sizing only. `notional = clamp(|target_weight − current_weight| × equity, 0, single_order_cash_limit)`. Buys additionally capped so post-fill cash weight ≥ `min_cash_weight` and post-fill position weight ≤ `max_position_weight`. No averaging-down logic at MVP.

### Limit price logic

- Buy: `limit_price = round_down_to_valid_krx_tick(last_price)`.
- Sell: `limit_price = round_up_to_valid_krx_tick(last_price)`.
- If bid/ask is available: buy uses `min(last, ask)`, sell uses `max(last, bid)`, then tick-round as above.
- Market orders are disabled (`allowed_order_types = [limit]`).

### No-chasing rule

A buy limit price may never exceed the signal-day reference price (`last_price` at proposal time) — no upward amendment, ever. After an expiry, at most **one** re-proposal per symbol per trading day is allowed, priced from the *new* quote but still capped at `reference_price × 1.005`. If the re-proposal also expires, the symbol is blocked for the day with reason code `no_chase_exhausted`. Mirror logic for sells (no downward chasing below `reference_price × 0.995`).

### Order expiry

`order_expiry_minutes = 30` (default). Rationale: on a daily-timeframe strategy a conservative limit order either fills near the reference price within minutes, or the price has moved and a resting order risks filling only on adverse movement (adverse selection). 30 minutes bounds that exposure while tolerating normal KRX intraday noise. Unapproved *proposals* expire at session close. A `RiskCheck` older than its `expires_at` (10 minutes, schemas.py) must be re-run at approval/submission time.

### Idempotency key construction

```text
idempotency_key = sha256(
  f"{policy_id}:{policy_version}:{strategy_id}:{strategy_version}:"
  f"{symbol}:{side}:{trading_date_iso}"
).hexdigest()[:32]
```

Deterministic per (policy, strategy, symbol, side, day). A second proposal attempt with the same key on the same day is the *re-proposal* slot; a third is rejected as duplicate.

### Stale data handling

- Proposal creation: quote older than **120 seconds** → proposal is created but flagged `stale_quote_warning`; the approval screen must show the quote age.
- Submission (post-approval): quote older than **30 seconds** → submission blocked, quote refreshed, limit price recomputed; if the recomputed price violates no-chasing, the proposal returns to the user.

### Required explanation fields

Each proposal persists: `strategy_id`, `strategy_version`, `signal_reason` (from `Signal.reason`), `reason_codes`, `current_weight`, `target_weight`, `weight_delta`, `quote_price`, `quote_age_seconds`, `limit_price`, `estimated_notional`, `estimated_cost_bps`, `stop_price_hint`, `take_profit_hint`, `risk_checks_passed[]`, `policy_version`.

### Approval screen fields

Symbol & name · side · quantity · limit price · notional (KRW) · current → target weight · plain-language signal reason · quote age · expiry time · risk checks passed (green list) · warnings (stale quote, loss pause proximity) · buttons: **Approve / Modify / Reject**.

### Modification rules

User may modify only: `quantity` (down only), `limit_price` (within ±2% of proposed, tick-valid, no-chasing still enforced for buys upward). Any modification re-runs the risk gate before the plan can become `user_approved`. Modified fields are audited with before/after state.

### Rejection rules

Rejection sets the plan to `cancelled` with `reason = user_rejected` and blocks the idempotency key for the day (no silent re-proposal of a rejected order). Rejection reasons are offered as picklist + free text and stored for the modification-rate metric used in the L3→L4 promotion gate.

### Audit events

`proposal_created`, `proposal_risk_checked`, `proposal_approved`, `proposal_modified`, `proposal_rejected`, `proposal_expired`, `order_submitted`, `order_filled`, `order_partially_filled`, `order_cancelled`, `order_expired`, `stale_quote_blocked`, `duplicate_order_blocked`.

### Tests Codex must write

`test_proposal_algorithm_deterministic_given_fixture`, `test_rebalance_band_skips_small_deltas`, `test_single_order_cash_limit_caps_notional`, `test_buy_limit_never_above_reference`, `test_one_reproposal_then_block`, `test_idempotency_key_stable_and_unique`, `test_risk_check_expiry_forces_rerun`, `test_modification_rerisks_before_approval`, `test_rejected_key_blocked_for_day`, `test_proposal_expires_at_session_close`.

---

## 5. Level 4 Guarded Autopilot Recipe

Level 4 submits user-pre-authorized orders automatically. It generates proposals with the **same algorithm as Level 4 does not exist** — it calls the Level 3 algorithm verbatim, then replaces the human approval step with this authority check sequence.

### Authority check sequence (all must pass, in order; first failure stops)

```text
1. kill_switch_engaged == False
2. authority_level == 4 and policy.execution_mode == guarded_autopilot
3. policy_version(plan) == policy_version(current policy)        # policy version consistency
4. broker health: heartbeat within 60s AND last API error > 5 min ago
5. trading session open AND now within [open+10min, close-20min]  # no auction-window auto-orders
6. quote_age_seconds <= 30
7. risk gate: full Section 6 matrix passes (fresh RiskCheck)
8. daily_order_count < max_daily_orders (3)
9. daily_turnover + order_notional/equity <= max_daily_turnover (0.20)
10. monthly loss state: pause/stop rules (below)
11. idempotency key unused (or in its single re-proposal slot)
-> all pass: status user_approved is replaced by system_authorized record,
   plan -> submitted via BrokerAdapter (Mock/Paper only in this stage)
```

The plan still traverses `draft → risk_checked → proposed → user_approved → submitted`; at Level 4 the `user_approved` transition is performed by the authority checker and the audit record stores `approved_by = "policy_authority_v<policy_version>"` instead of a user action. The state machine itself is never bypassed.

### Trading session / time window rules

Auto-orders only during KRX continuous session, excluding the first 10 and last 20 minutes (opening/closing auction volatility). Outside the window, plans queue as Level 3 proposals instead (human may still approve).

### Broker health check

A health probe (account fetch on Mock/Paper) must have succeeded within 60 seconds. Two consecutive failures → demote to Level 3 (`authority_demoted_l4_to_l3`, trigger `broker_health`).

### Quote freshness rule

`stale_quote_max_age_seconds = 30` for any automatic submission. Rationale: with limit-only orders the worst stale-quote outcome is a mispriced limit; 30 seconds bounds price drift on liquid KRX names to roughly intraday-noise scale while tolerating normal feed latency. One stale block → refresh and retry once; > 50% stale rate over 10 attempts → demote to Level 3.

### Policy version consistency rule

Every plan and risk check carries `policy_version`. If the user edits the policy between plan creation and submission, the version comparison fails, the plan is cancelled, and planning re-runs under the new version. Audit: `policy_version_mismatch`.

### Cash and exposure constraints

Post-fill projections must satisfy: cash weight ≥ 0.20, position weight ≤ 0.15, sector weight ≤ 0.40. Projections use limit price (worst case for buys). Violations block the order, never resize it silently at Level 4 (resizing is a Level 3 human decision).

### Daily order count and turnover constraints

`max_daily_orders = 3` automatic submissions per day (approved-by-human Level 3 orders count toward it too — the budget is shared). `max_daily_turnover = 0.20`: sum of |filled + open notional| / equity per day. Both reset at session open; both block (not resize) on breach.

### Monthly loss pause and stop

- Month-to-date return ≤ **-5%** (`monthly_loss_pause_new_buys`): all automatic *buys* stop; sells remain automatic; new-buy proposals appear only as Level 3 items. Audit: `loss_pause_engaged`.
- Month-to-date return ≤ **-10%** (`monthly_loss_stop_all_autotrading`): kill-switch path triggers (Section 3); all autotrading stops; system drops to Level 2. Audit: `loss_stop_engaged`.
- Both compare `PortfolioSnapshot.monthly_loss_ratio` against thresholds at every authority check; release only by calendar month rollover **plus** explicit user re-confirmation.

### Unfilled order handling

At `order_expiry_minutes = 30`: cancel at broker, mark `expired`, evaluate the single re-proposal slot (Section 4 no-chasing). Partial fills: the filled part stands; the remainder is cancelled at expiry and the residual delta is left to the next rebalance cycle (no immediate top-up order — prevents drip-feeding around the order-count limit).

### Duplicate order handling

The idempotency key is checked at proposal creation **and** again at submission (TOCTOU guard). A duplicate at either point blocks with `duplicate_order_blocked`. Broker-side duplicate defense: `OrderPlan.order_plan_id` is sent as the client order reference; a resubmission of the same plan id must be rejected by the adapter.

### Cancellation and re-proposal rules

Automatic cancellations happen only for: expiry, policy version mismatch, demotion, kill switch. The user may cancel any open order at any time from the UI (this never requires Level 4 authority). Re-proposal: max one per symbol per day, new quote, no-chasing cap, full authority sequence re-run.

### Kill switch behavior

Single UI control + API route, no confirmation dialog, idempotent. Engaging it: cancels unsubmitted plans, requests cancellation of open broker orders, sets `authority_level = 2`, blocks all proposal creation, writes `kill_switch_engaged` before any other action completes. If the audit write fails, the system must treat the kill switch as engaged (fail-closed).

### Automatic fallback behavior

Demotion triggers in Section 3 are evaluated inside the authority check sequence; demotion takes effect before the next order can be processed. Fallback is always to a *less* autonomous level, never sideways. The system must remain fully operable at Level 2 (suggestions) regardless of any Level 3/4 component failure.

### Audit events

All Level 3 events, plus: `autopilot_order_authorized`, `autopilot_order_blocked` (with failing check name), `authority_demoted_l4_to_l3`, `authority_demoted_to_l2`, `loss_pause_engaged`, `loss_stop_engaged`, `kill_switch_engaged`, `kill_switch_released`, `broker_health_failed`, `policy_version_mismatch`.

### Tests Codex must write

`test_authority_sequence_order_and_short_circuit`, `test_l4_reuses_l3_proposal_algorithm`, `test_session_window_blocks_auction_periods`, `test_broker_health_two_failures_demote`, `test_stale_quote_blocks_submission`, `test_policy_version_mismatch_cancels_plan`, `test_daily_order_budget_shared_with_manual`, `test_turnover_cap_blocks_not_resizes`, `test_loss_pause_blocks_buys_allows_sells`, `test_loss_stop_engages_kill_path`, `test_partial_fill_residual_not_topped_up`, `test_duplicate_blocked_at_submission_time`, `test_kill_switch_fail_closed_on_audit_error`.

---

## 6. Risk Limit Matrix

All checks run in `risk/gatekeeper.py`; "Block" means the order/plan is rejected with a reason code, never silently resized at Level 4.

| Limit | Default | Source | Calculation Method | Pass Condition | Fail Action | Audit Event | Codex Test |
|---|---|---|---|---|---|---|---|
| single_order_cash_limit | 1,000,000 KRW | UserPolicy (exists) | `intent.notional` | notional ≤ limit | Block order | `risk_single_order_cash_blocked` | `test_gate_single_order_cash_limit` |
| max_position_weight | 0.15 | UserPolicy (exists) | post-fill `(position_value + buy_notional) / equity` at limit price | ≤ 0.15 | Block order | `risk_position_weight_blocked` | `test_gate_max_position_weight` |
| max_sector_weight | 0.40 | UserPolicy (exists) | post-fill sector sum / equity | ≤ 0.40 | Block order | `risk_sector_weight_blocked` | `test_gate_max_sector_weight` |
| max_daily_orders | 3 | guardrail baseline (extend UserPolicy) | count of plans reaching `submitted` today | count < 3 before submit | Block order | `risk_daily_orders_blocked` | `test_gate_max_daily_orders` |
| max_daily_turnover | 0.20 | guardrail baseline (extend UserPolicy) | Σ\|submitted notional\| / equity today | projected ≤ 0.20 | Block order | `risk_daily_turnover_blocked` | `test_gate_max_daily_turnover` |
| min_cash_weight | 0.20 | UserPolicy (exists) | post-fill `cash / equity` at limit price | ≥ 0.20 | Block buy | `risk_min_cash_blocked` | `test_gate_min_cash_weight` |
| daily_loss_limit | -0.03 | UserPolicy (exists) | `snapshot.daily_loss_ratio` | > -0.03 | Halt new buys today; L4→L3 demotion | `risk_daily_loss_halt` | `test_gate_daily_loss_limit` |
| monthly_loss_pause_new_buys | -0.05 | UserPolicy `monthly_loss_limit` (exists) | `snapshot.monthly_loss_ratio` | > -0.05 | Pause auto-buys; buys become L3-only | `loss_pause_engaged` | `test_gate_monthly_loss_pause` |
| monthly_loss_stop_all_autotrading | -0.10 | guardrail baseline (extend UserPolicy) | `snapshot.monthly_loss_ratio` | > -0.10 | Kill-switch path; drop to L2 | `loss_stop_engaged` | `test_gate_monthly_loss_stop` |
| stale_quote_max_age_seconds | 30 (auto-submit) / 120 (proposal) | this recipe §4–5 | `now - quote_time` | age ≤ threshold | Block submit, refresh once; warn on proposal | `stale_quote_blocked` | `test_gate_stale_quote` |
| order_expiry_minutes | 30 | this recipe §4 | `now - submitted_at` | open order age < 30 min | Cancel at broker, mark `expired` | `order_expired` | `test_gate_order_expiry` |
| no_chasing_rule | buy ≤ ref×1.005, 1 re-proposal/day | this recipe §4 | compare new limit to reference price; count key uses | within cap and ≤ 1 retry | Block re-proposal for the day | `no_chase_blocked` | `test_gate_no_chasing` |
| unfilled_order_rule | cancel at expiry; residual waits | this recipe §5 | partial-fill residual handling | no top-up order created | Block top-up | `unfilled_residual_deferred` | `test_gate_unfilled_residual` |
| duplicate_idempotency_rule | unique per (policy, strategy, symbol, side, day) | schemas.py `OrderPlan.idempotency_key` | key lookup at creation and submission | key unused (or re-proposal slot) | Block order | `duplicate_order_blocked` | `test_gate_duplicate_idempotency` |
| broker_health_rule | heartbeat ≤ 60s, 2 fails → demote | this recipe §5 | adapter health probe timestamps | healthy | Block submit; demote on 2nd fail | `broker_health_failed` | `test_gate_broker_health` |
| policy_version_rule | plan version == current | schemas.py `policy_version` fields | integer compare | equal | Cancel plan, replan | `policy_version_mismatch` | `test_gate_policy_version` |
| kill_switch_rule | not engaged | this recipe §3/§5 | boolean check, first in sequence | `False` | Block everything; cancel open | `kill_switch_engaged` | `test_gate_kill_switch_first` |

---

## 7. Strategy Recipe YAML Schema

Schema (validated by `strategies/loader.py`; extends the existing `StrategyRecipe` model — new fields are additive):

```yaml
strategy_id: string                # unique, snake_case
version: string                    # semver; any rule change bumps it
description: string
market: KR_STOCK
timeframe: daily
universe_filter:
  min_avg_daily_value: float       # KRW, default 5_000_000 (UserPolicy)
  exclude_blocklist: true
  max_universe_size: int
features:                          # named, formula-bearing inputs
  - name: string
    formula: string                # plain math over OHLCV, deterministic
    lookback_days: int
    source_citation: string        # mark [PENDING VERIFICATION] if unchecked
entry_rules:                       # ALL must hold; plain predicates over features
  - string
exit_rules:                        # ANY triggers exit/trim
  - string
no_chasing_rules:
  max_premium_over_reference: float    # e.g. 0.005
  max_reproposals_per_day: int         # 1
position_sizing:
  method: capped_score_weight | inverse_volatility   # research methods not valid here
  max_target_weight: float
  min_rebalance_band: float
risk_rules:                        # strategy-local, AND-ed with policy gates
  - string
rebalance: daily | weekly | monthly
execution_permissions:
  allowed_order_types: [limit]
  market_orders: disabled
validation:                        # filled by the Section 8 protocol, not by hand
  backtest_report: path
  walk_forward_windows: int
  oos_sharpe: float
  pbo_estimate: float
  deflated_sharpe_pass: bool
  paper_trading_days: int
promotion_status: draft | validated_l3 | validated_l4 | revoked
allowed_execution_levels: []       # subset of [3,4]; loader enforces vs promotion_status
audit_metadata:
  authored_by: string
  authored_at: date
  reviewed_by: string
  sources: []
```

Loader invariants Codex must enforce: `allowed_execution_levels` ⊆ levels earned by `promotion_status` (`validated_l3` → `[3]`, `validated_l4` → `[3,4]`, otherwise `[]`); `position_sizing.method` must be in the implemented allowlist; any validation field missing → `promotion_status` forced to `draft`.

Example (conservative successor to `quantpilot/docs/strategy_specs/pullback_trend_v1.yaml`):

```yaml
strategy_id: pullback_trend_v2
version: "2.0"
description: Buy liquid uptrending KRX stocks on oversold pullbacks confirmed by volume; exit on trend break or overheat.
market: KR_STOCK
timeframe: daily
universe_filter:
  min_avg_daily_value: 5000000
  exclude_blocklist: true
  max_universe_size: 50
features:
  - name: trend_filter
    formula: close > sma(close, 120)
    lookback_days: 120
    source_citation: "Moskowitz, Ooi & Pedersen (2012) JFE 104(2) [PENDING VERIFICATION]"
  - name: pullback_rsi
    formula: rsi(close, 14)
    lookback_days: 14
    source_citation: "general mean-reversion design pattern — needs source verification"
  - name: volume_confirm
    formula: volume / sma(volume, 20)
    lookback_days: 20
    source_citation: "general volume-confirmation design pattern — needs source verification"
entry_rules:
  - trend_filter is true
  - pullback_rsi crossed up through 30 within last 3 sessions
  - volume_confirm >= 1.2 on the cross-up session
exit_rules:
  - close < sma(close, 60)            # trend break
  - pullback_rsi > 75                  # overheated
  - close <= stop_price_hint           # hard stop from Signal
no_chasing_rules:
  max_premium_over_reference: 0.005
  max_reproposals_per_day: 1
position_sizing:
  method: capped_score_weight
  max_target_weight: 0.10
  min_rebalance_band: 0.01
risk_rules:
  - limit orders only
  - respect policy max position weight
  - respect minimum cash weight
  - stop_loss_pct 0.03 from entry fill price
  - portfolio drawdown 0.20 halts new entries (circuit breaker)
rebalance: weekly
execution_permissions:
  allowed_order_types: [limit]
  market_orders: disabled
validation:
  backtest_report: docs/quant_recipes/validation/pullback_trend_v2_backtest.md
  walk_forward_windows: 5
  oos_sharpe: null            # filled by protocol run
  pbo_estimate: null
  deflated_sharpe_pass: null
  paper_trading_days: 0
promotion_status: draft
allowed_execution_levels: []
audit_metadata:
  authored_by: fable5-recipe-architect
  authored_at: 2026-06-12
  reviewed_by: ""
  sources:
    - "Jegadeesh & Titman (1993) JF 48(1) [PENDING VERIFICATION]"
    - "Pardo (2008) walk-forward standard [PENDING VERIFICATION]"
```

Risk-matrix consistency note: `max_target_weight 0.10` with portfolio-drawdown circuit breaker at 0.20 keeps the drawdown limit ≥ 2× the position cap; `stop_loss_pct 0.03` aligns with the gatekeeper default; sizing remains under the policy cap of 0.15.

---

## 8. Backtest, Walk-Forward, and Overfitting Protocol

| Element | Specification | Mandatory L3 | Mandatory L4 |
|---|---|---|---|
| Data versioning | Every run pins a dataset id + hash; results without a dataset hash are invalid | ✔ | ✔ |
| Next-bar execution | Signals computed on bar *t* execute at bar *t+1* open (or *t+1* limit logic); same-bar fills forbidden | ✔ | ✔ |
| Transaction costs | ≥ 15 bps per side (commission + KRX fees); parameterized, never 0 | ✔ | ✔ |
| Slippage | Limit-order model: fill only if next-bar low ≤ buy limit (high ≥ sell limit); plus 1×ATR-scaled penalty for marketable prices | ✔ | ✔ |
| Tax assumptions | KR securities transaction tax on sells modeled as flat per-side bps (exact rate is a Codex data dependency); flag if omitted | ✔ | ✔ |
| Liquidity filter | `min_avg_daily_value ≥ 5,000,000 KRW`; single order ≤ 5% of 20-day average daily value | ✔ | ✔ |
| Market regime breakdown | Report Sharpe/MDD separately for at least: rising, falling, and high-volatility regimes of the index | ✔ | ✔ |
| Walk-forward validation | ≥ 5 windows; in-sample ≥ 3× out-of-sample length (Pardo 2008); all windows must complete | ✔ | ✔ |
| Parameter sensitivity | ±20% perturbation of every tunable parameter; strategy fails if OOS Sharpe sign flips | ✔ | ✔ |
| Turnover | Annualized turnover reported; must be consistent with `max_daily_turnover = 0.20` | ✔ | ✔ |
| MDD | Out-of-sample max drawdown ≤ strategy circuit-breaker level (e.g. 0.20) | ✔ | ✔ |
| Sharpe / Sortino | Reported per window and aggregate; OOS Sharpe > 0 minimum | ✔ | ✔ |
| Deflated Sharpe check | DSR computed with the true number of trials logged during research; pass requires DSR > 0 at 95% confidence (Bailey & López de Prado 2014) | ✔ | ✔ |
| PBO warning | CSCV-based PBO estimate; PBO ≥ 0.5 → hard reject; 0.3–0.5 → warning banner on every report | ✔ | ✔ |
| Paper-trading period | ≥ 20 trading days at Level 2/3 paper before `validated_l3`; ≥ 60 trading days (incl. ≥ 30 decided proposals) before `validated_l4` | ✔ (20d) | ✔ (60d) |
| Promotion gates | All above pass → `promotion_status` may advance; recorded in YAML `validation` block + DB | ✔ | ✔ |
| Rejection gates | Any hard fail → `promotion_status: draft`; previously promoted strategy failing a re-validation → `revoked` and immediate demotion of any policy using it | ✔ | ✔ |

Trial counting is mandatory: every backtest run (including discarded variants) increments a per-strategy trial counter stored in the DB; DSR and PBO must be computed against that counter, not against the survivors. Framework choice for implementation: vectorbt-style vectorized runs for sweeps, with one event-driven confirmation pass (Backtrader/Nautilus-style next-bar fills) before promotion — both are design patterns, not mandatory dependencies.

---

## 9. Portfolio Sizing Recipe

| Method | Status | When to use | Required inputs | Failure modes | Codex priority | Validation tests |
|---|---|---|---|---|---|---|
| Capped score weighting (**default**) | MVP | Always, until something else is validated | `quant_score` per symbol, policy caps | Score scale drift; concentration if few candidates (mitigated by caps + cash floor) | P0 | `test_sizing_capped_score_weights_sum`, `test_sizing_respects_policy_caps` |
| Inverse volatility | Allowed L3/4 after validation | When realized vol differs widely across holdings | 60d realized vol per symbol | Vol estimate noise; low-vol value traps; regime shifts | P1 | `test_sizing_inverse_vol_monotonic`, full Section 8 re-run |
| Risk parity (ERC) | Research / advanced | Multi-sector portfolios ≥ 8 names | Covariance matrix (Ledoit-Wolf-style shrinkage) | Covariance estimation error; solver non-convergence | P3 | offline notebook + Section 8 before any wiring |
| HRP | Research / advanced | When correlation structure is unstable (De Prado 2016) | Correlation matrix, linkage method | Cluster instability across windows; small-universe degeneracy | P3 | walk-forward weight-stability report + Section 8 |
| Black-Litterman | Research-only | Only after analyst/user views are calibrated with a track record | Equilibrium weights, view vector + confidence | Garbage views → garbage posterior; view-confidence miscalibration | P4 | view-calibration study precedes any backtest |
| RL target-weight policy | Research-only | Only after full Section 10 promotion ladder | Trained policy + policy card | Reward hacking; regime overfit; silent degradation | P5 (last) | entire Section 10 ladder |

Default algorithm (P0), deterministic:

```text
candidates = signals with action == buy_ready, sorted by quant_score desc
raw_w[i] = quant_score[i] / sum(quant_scores)
w[i] = min(raw_w[i] * (1 - min_cash_weight), strategy.max_target_weight, policy.max_position_weight)
renormalize residual to cash; cash_target = max(min_cash_weight, 1 - sum(w))
```

---

## 10. RL Contract

RL is a research pathway producing **policy suggestions**, never orders. Hard rules (restating the non-negotiables): no broker API access from any RL component; no raw order fields in any RL output; outputs limited to `target_weight_delta` or `strategy_selection`, mapped onto `Signal.target_weight_hint` and processed by the standard planner → risk gate → permission ladder; a deterministic fallback strategy (`pullback_trend_v2` class) must be active whenever an RL policy is; every deployed policy ships a human-readable policy card (objective, training data range, known failure regimes, fallback trigger).

**Safety overrides (evaluated before reward, cannot be disabled by the agent)**

1. Hard stop-loss breach on a position → environment forces the delta that closes it; agent action ignored.
2. Portfolio drawdown ≥ circuit-breaker level → all positive deltas masked to 0.
3. Policy cap violation (position/sector/cash) → action clipped to the feasible boundary, clip event logged.
4. Episode terminates on simulated `monthly_loss_stop` (-0.10) — the agent must experience the stop, not trade through it.

**Observation space** — market features: per-symbol returns (1/5/20/60d), realized vol (20/60d), RSI(14), volume ratio, distance from SMA120; portfolio state: current weights, cash weight, unrealized PnL per position, drawdown from peak; risk state: daily/monthly loss ratios, remaining daily order budget, remaining turnover budget; macro regime: index trend label {up, down, flat}, index vol regime {low, high}. All features computable from existing OHLCV fixtures; no order-book data assumed.

**Action space** (discrete; both variants implemented behind one interface in `rl/outputs.py`)

- `target_weight_delta`: per decision step, one symbol from the active universe plus a delta from `{-0.05, -0.02, 0.00, +0.02, +0.05}`, with `0.00` (HOLD-equivalent, defensive) always available; post-clip by safety overrides; max one nonzero delta per day to respect `max_daily_orders`.
- `strategy_selection`: choice among strategies whose `promotion_status` permits the current level, plus `CASH` (defensive); switch frequency capped at once per week to bound turnover.

**Reward function**

```text
r_t = Δlog(NAV_t) − λ_dd · max(0, drawdown_t − 0.05)² − λ_to · turnover_t − λ_clip · clip_events_t
λ_dd = 10.0, λ_to = 1.0, λ_clip = 0.1 (initial; tuned only on training folds)
normalization: running z-score over training data only
```

Drawdown is penalized quadratically past 5% so the agent learns risk control, not just return; clip penalties teach it to stay inside bounds rather than rely on the clipper.

**Constraints** — long-only weights in [0, 0.15] per symbol, cash ≥ 0.20, sector ≤ 0.40 (identical to the deterministic gates: the environment mirrors `gatekeeper.py` logic so the policy never trains against a looser world than it will live in).

**Offline training protocol** — algorithm: PPO first (discrete-friendly, stable baseline; FinRL design pattern, `[PENDING VERIFICATION]`); training data: in-sample windows from Section 8 splits only; ≥ 500 training episodes, evaluation every 25 on a held-out fold; early stopping on evaluation Sortino with patience 5; convergence: evaluation reward std/mean < 0.10 over last 5 evaluations; hard budget 2,000 episodes — non-convergence is a rejection, not a reason to extend.

**Evaluation protocol** — identical Section 8 protocol applied to the frozen policy (next-bar execution, costs, slippage, regime breakdown, DSR with trial counter including all training runs, PBO across hyperparameter variants), **plus** behavioral checks: clip-event rate < 5% of steps; turnover within `max_daily_turnover`; performance ≥ deterministic fallback on OOS or the policy is rejected.

**Promotion ladder** (each stage gates the next; any failure returns to research)

1. Offline backtest pass (Section 8 full protocol)
2. Walk-forward pass (≥ 5 windows)
3. Paper trading ≥ 60 trading days, policy outputs logged but `order_submission_enabled = False`
4. Level 3 observation ≥ 30 decided proposals: RL suggestions surface as proposals; user approval rate ≥ 80%
5. Level 4 admission with **halved caps** for the first 60 days: `max_daily_orders = 1`, deltas restricted to `{-0.02, 0, +0.02}`

**Forbidden behaviors** — emitting symbols outside the validated universe; emitting any field that resembles an order (price, quantity, side, account); modifying its own reward, caps, or the safety overrides; trading during a loss pause/stop; running without an active fallback strategy; bypassing `Signal` schema validation.

**Codex tests** — `test_rl_output_schema_only_delta_or_selection`, `test_rl_delta_grid_bounded`, `test_rl_clip_logged_and_penalized`, `test_rl_cannot_construct_order_intent` (import/type-level guard: `rl/` must not import broker or execution modules), `test_rl_env_mirrors_gatekeeper_limits`, `test_rl_fallback_engages_on_policy_error`, `test_rl_promotion_ladder_gates_enforced`, `test_rl_halved_caps_in_first_l4_period`.

---

## 11. Codex Implementation Contract

**Schemas to create or extend** (`quantpilot/packages/core/schemas.py`)

1. Extend `UserPolicy`: `max_daily_orders: int = 3`, `max_daily_turnover: float = 0.20`, `monthly_loss_stop: float = -0.10` (validator: must be ≤ `monthly_loss_limit`), `stale_quote_max_age_seconds: int = 30`, `order_expiry_minutes: int = 30`, `authority_level: int = 2` (1–4), `kill_switch_engaged: bool = False`.
2. Extend `StrategyRecipe` with the Section 7 fields (`universe_filter`, `features`, `no_chasing_rules`, `execution_permissions`, `validation`, `promotion_status`, `allowed_execution_levels`, `audit_metadata`) — keep existing fields intact for `pullback_trend_v1.yaml` compatibility.
3. New `ProposalExplanation` model (Section 4 explanation fields), referenced from `OrderPlan`.
4. New `AuthorityCheckResult` model: ordered list of `(check_name, passed, detail)`, `authorized: bool`, `policy_version`.
5. New `GuardrailState` model: daily order count, daily turnover used, loss-pause/stop flags, kill-switch state, last broker heartbeat.

**Functions to implement**

- `risk/gatekeeper.py`: one pure function per Section 6 row, plus `run_gate(plan, snapshot, policy, state) -> RiskCheck` executing them in matrix order.
- `execution/state_machine.py`: `authorize_level4(plan, policy, state, quote) -> AuthorityCheckResult` implementing the Section 5 sequence; `expire_open_orders(now)`; `engage_kill_switch(reason)` (fail-closed on audit error).
- `portfolio/planner.py`: Section 4 proposal algorithm; Section 9 capped score weighting; `idempotency_key(...)` exactly per Section 4.
- `strategies/loader.py`: Section 7 schema validation + promotion/level invariants.
- `rl/outputs.py`: `TargetWeightDelta` and `StrategySelection` output types only; no other RL output type may exist.

**Services / API routes** (`quantpilot/services/api/routers/`)

- `proposals.py`: `GET /proposals/pending`, `POST /proposals/{id}/approve`, `POST /proposals/{id}/modify`, `POST /proposals/{id}/reject`.
- `autopilot.py`: `GET /autopilot/state`, `POST /autopilot/kill-switch` (engage; no body required), `POST /autopilot/kill-switch/release` (two-step confirmation payload), `POST /autopilot/promote`, `POST /autopilot/demote`.
- Extend `harness.py` smoke route to cover the Level 3 path end-to-end.

**UI changes** (`quantpilot/apps/web/`)

Approval queue screen (Section 4 fields, Approve/Modify/Reject); guardrail dashboard (every Section 6 limit with current consumption); kill-switch button visible on every trading screen; promotion wizard implementing the Section 3 confirmation steps; warnings from Section 13 rendered verbatim.

**Audit events** — implement every event named in Sections 3–6 in `db/audit.py` as constants; an event name not in the constants list must fail tests.

**Feature flags** — `level3_enabled` (default off), `level4_enabled` (default off; requires `level3_enabled`), `rl_research_mode` (default off; cannot be on with `level4_enabled` for the same policy until ladder stage 5), `sizing_inverse_vol_enabled` (default off).

**Fallback behavior** — any unhandled exception in proposal creation, authority check, or submission paths: log, demote per Section 3, continue serving Level 2. Level 2 must have zero imports from Level 3/4 modules so it cannot be broken by them.

**Migration notes** — add new `UserPolicy` columns with defaults so existing fixture policies remain valid; backfill `authority_level = 2`; `pullback_trend_v1.yaml` loads under the extended schema with `promotion_status: draft`, `allowed_execution_levels: []`.

**Test files to create** — `tests/unit/test_level3_proposals.py`, `tests/unit/test_risk_matrix.py`, `tests/unit/test_authority_checks.py`, `tests/unit/test_strategy_loader_v2.py`, `tests/unit/test_rl_contract.py`, `tests/integration/test_level3_flow.py`, `tests/integration/test_level4_guarded_flow.py`, `tests/integration/test_fallback_and_kill_switch.py`.

**Smoke tests** — extend `jobs/run_smoke.py`: policy → signals → plan → proposals → (approve one, reject one, expire one) → mock fill → report; then a Level 4 variant: authorize → submit → fill → report, ending with kill-switch engagement and verification that everything halts.

**Documentation files** — `docs/quant_recipes/validation/` (protocol run reports), `docs/claude/level34_operations_guide.md` (operator guide using Section 13 warnings), update `docs/pre_harness_report.md` cross-references.

---

## 12. Acceptance Test Suite

### Unit tests

| Test name | Purpose | Setup | Expected result |
|---|---|---|---|
| `test_proposal_algorithm_deterministic_given_fixture` | Same inputs → identical proposals | Fixed snapshot, signals, quotes from `tests/fixtures/ohlcv.json` | Two runs produce byte-identical plans and keys |
| `test_idempotency_key_stable_and_unique` | Key spec per Section 4 | Same/different (symbol, side, day) tuples | Same tuple → same key; any field change → different key |
| `test_buy_limit_never_above_reference` | No-chasing core invariant | Quote sequence where price rises after proposal | Limit price ≤ reference; re-proposal capped at ×1.005 |
| `test_gate_*` (17 tests, one per Section 6 row) | Each limit blocks exactly at threshold | Plan crafted at limit, just under, just over | Under passes; at-limit passes (≤/≥ per spec); over blocks with named audit event |
| `test_strategy_loader_rejects_unearned_level` | Promotion invariant | YAML with `promotion_status: draft`, `allowed_execution_levels: [3]` | Loader raises validation error |
| `test_rl_output_schema_only_delta_or_selection` | RL output restriction | Attempt to construct other output types | Type system / validator rejects |

### Integration tests

| Test name | Purpose | Setup | Expected result |
|---|---|---|---|
| `test_level3_flow_end_to_end` | Full L3 happy path | Policy v1, fixture signals, MockBroker | draft → risk_checked → proposed → user_approved → submitted → filled; report generated; `live_trading_enabled == False` |
| `test_policy_edit_invalidates_pending_plans` | Version consistency | Approve a proposal after bumping policy version | Submission blocked, `policy_version_mismatch`, replan occurs |
| `test_state_machine_no_skip_transitions` | State machine integrity | Attempt proposed → submitted directly | Transition rejected |

### Paper broker tests

| Test name | Purpose | Setup | Expected result |
|---|---|---|---|
| `test_paper_fill_respects_limit_logic` | Realistic fills | Paper broker with next-bar prices crossing/not crossing limit | Fill only when bar range crosses limit price |
| `test_paper_partial_fill_residual_deferred` | Section 5 unfilled rule | Partial fill then expiry | Remainder cancelled; no top-up order same day |

### Risk gate tests

| Test name | Purpose | Setup | Expected result |
|---|---|---|---|
| `test_gate_runs_in_matrix_order_and_short_circuits` | Deterministic gate order | Plan failing two checks | Only first failure reported as blocker; both audited |
| `test_gate_never_resizes_at_level4` | Block-don't-resize rule | Oversized buy at L4 | Order blocked, quantity unchanged |
| `test_gate_uses_post_fill_projection` | Worst-case exposure math | Buy that passes pre-fill but breaches post-fill cash | Blocked on `min_cash_weight` |

### Approval workflow tests

| Test name | Purpose | Setup | Expected result |
|---|---|---|---|
| `test_modification_rerisks_before_approval` | Modified orders re-checked | User lowers quantity, raises price +1.9% | New RiskCheck created before `user_approved` |
| `test_modification_bounds_enforced` | Modification limits | Price change +2.5%, quantity increase | Both rejected |
| `test_rejected_key_blocked_for_day` | No silent re-proposal | Reject, then planner re-runs | No new proposal for that (symbol, side, day) |
| `test_risk_check_expiry_forces_rerun` | 10-minute `RiskCheck.expires_at` | Approve 11 minutes after check | Submission blocked until fresh check passes |

### Guarded autopilot tests

| Test name | Purpose | Setup | Expected result |
|---|---|---|---|
| `test_authority_sequence_order_and_short_circuit` | Section 5 sequence | Instrumented checks, kill switch engaged | Check 1 fails; checks 2–11 never run |
| `test_l4_records_system_authorization` | Audit trail of auto-approval | Successful L4 submission | `approved_by == "policy_authority_v<N>"` in audit |
| `test_daily_order_budget_shared_with_manual` | Shared budget | 2 manual + 1 auto order today | 4th order blocked |
| `test_loss_pause_blocks_buys_allows_sells` | -5% pause semantics | `monthly_loss_ratio = -0.06` | Auto-buys blocked; auto-sells proceed |
| `test_session_window_blocks_auction_periods` | Time window | Clock at open+5min and close-10min | Auto-submit blocked; queued as L3 proposal |

### Fallback / kill-switch tests

| Test name | Purpose | Setup | Expected result |
|---|---|---|---|
| `test_kill_switch_halts_all_paths` | Full stop | Engage during pending submissions | Plans cancelled, broker cancels requested, `authority_level == 2`, no new proposals |
| `test_kill_switch_fail_closed_on_audit_error` | Fail-closed | Audit writer raises | System behaves as if kill switch engaged |
| `test_level4_demotes_to_level3_on_trigger` | Auto-demotion | 3 consecutive gate blocks in one session | `authority_demoted_l4_to_l3` emitted; next order requires approval |
| `test_loss_stop_engages_kill_path` | -10% hard stop | `monthly_loss_ratio = -0.11` | Kill-switch path runs; system at Level 2 |
| `test_level2_operates_with_l34_modules_broken` | Fallback isolation | Monkeypatch L3/L4 modules to raise on import/use | Level 2 signal + suggestion flow still passes |

### Report tests

| Test name | Purpose | Setup | Expected result |
|---|---|---|---|
| `test_operation_report_live_flag_always_false` | Non-negotiable assertion | Generate reports across all flows | `live_trading_enabled == False` in every report |
| `test_report_links_all_audit_events` | Audit completeness | L3 + L4 smoke flows | Every emitted event id referenced; `audit_event_count` matches |
| `test_paper_validation_report_contents` | Promotion evidence | 20-day simulated paper run | Report includes fills, blocks, demotions, modification rate, drawdown vs limits |

---

## 13. Human-Readable Warnings

- **Approval-based trading (Level 3):** "QuantPilot prepares orders, but nothing is sent until you approve it. Review the price, amount, and reason before approving. You are the final decision-maker on every order."
- **Guarded autopilot (Level 4):** "Automatic trading is ON within the limits you set. QuantPilot may submit orders without asking you first. Check your limits now — they are the only thing between a signal and an order. The kill switch stops everything instantly."
- **Monthly loss pause:** "Your account is down more than 5% this month. Automatic buying is paused. Sells still work, and you can still approve buys manually. Buying resumes next month only after you confirm."
- **Monthly loss stop:** "Your account is down more than 10% this month. All automatic trading has stopped. QuantPilot is now in suggestion-only mode. This is a protective stop, not an error."
- **Strategy underperformance:** "This strategy is performing worse than its validation results predicted. Past test results never guarantee future returns. Consider demoting it to suggestion-only mode."
- **Stale data:** "The price shown is more than 30 seconds old. Orders based on stale prices may execute far from the price you see. QuantPilot blocked the automatic order; refresh and review before proceeding."
- **Unfilled orders:** "This order expired without filling (or filled only partially). QuantPilot will not chase the price. The remaining amount waits for the next rebalance."
- **Broker connection failure:** "QuantPilot cannot reach the broker right now. Automatic trading is suspended and the system has dropped to approval mode. Your existing positions are unaffected by this app — check your broker directly if concerned."
- **RL research mode:** "An experimental AI policy is suggesting portfolio changes. It cannot place orders. Its suggestions pass the same risk checks as everything else, and a conventional strategy is running as backup. Treat its output as research, not advice."
- **Transition to higher authority level:** "You are about to give QuantPilot more autonomy. Re-read every limit on this screen — after this step, orders inside these limits will not wait for your approval. You can return to approval mode or press the kill switch at any time."

---

## 14. Final Recommendation to Codex

Implement in this exact order; do not start a later item before the earlier item's tests pass.

1. **Level 3 order proposal and approval flow** — Section 4 algorithm in `portfolio/planner.py`, proposal routes, approval screen, idempotency keys, expiry, audit events. Exit criterion: `test_level3_flow_end_to_end` green on MockBroker.
2. **Risk limit matrix and deterministic risk gate extensions** — all 17 Section 6 rows in `risk/gatekeeper.py`, `UserPolicy` extensions, `GuardrailState`. Exit criterion: all `test_gate_*` green, gate order test green.
3. **Level 4 guarded execution with MockBroker/PaperBroker only** — Section 5 authority sequence, demotion triggers, kill switch (fail-closed), session windows. Exit criterion: guarded autopilot and fallback/kill-switch test groups green.
4. **Paper-trading validation reports** — Section 8 protocol runner, trial counter, DSR/PBO computation, validation report generation into `docs/quant_recipes/validation/`. Exit criterion: report tests green; `pullback_trend_v2` produces a complete (even if failing) validation report.
5. **Broker adapter integration only after guarded tests pass** — real adapter work begins only when items 1–4 are green for ≥ 60 paper trading days; `BrokerMode` stays `mock`/`paper`; `live_disabled` remains the default; live enablement is a user-and-gates decision outside this recipe's authority.
6. **RL research mode last** — Section 10 environment, output types, ladder enforcement; behind `rl_research_mode` flag, incompatible with `level4_enabled` until ladder stage 5.

**Self-Check Summary**

- Pass — Authority ladder and warnings are written in plain language a non-technical owner can follow (Sections 3, 13).
- Pass — Architectural decisions are made, not deferred: limit-only orders, 30s/120s staleness, 30-minute expiry, one re-proposal, capped score weighting first (Sections 4, 5, 9).
- Pass — Every automatic order path has risk gate, policy version check, stale quote check, duplicate check, and audit logging (Sections 5, 6).
- Pass — Level 4 has explicit demotion triggers and a fail-closed kill switch (Sections 3, 5).
- Pass — RL is bounded to `target_weight_delta` / `strategy_selection`, research-gated, with fallback and forbidden-behavior tests (Section 10).
- Pass — Advanced portfolio methods are staged behind validation gates with priorities P1–P5 (Section 9).
- Pass — Overfitting controls (walk-forward, DSR, PBO with trial counting, parameter sensitivity, regime breakdown) are mandatory before promotion (Section 8).
- Pass — MVP rules vs research-only enhancements are separated throughout (Sections 1, 9, 10, 11).
- Pass — No live broker code, credentials, or raw executable orders appear; sources without in-repo verification are marked `[PENDING VERIFICATION]` or "needs source verification" (Sections 2, 7).
- Pass — Implementation order for Codex is explicit and gated (this section).
