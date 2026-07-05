# Local Real-Data Backtest Validation Report

Date: 2026-07-06

## Summary

First execution of the Stage 03 backtest protocol against **real KRX market
data** (previously fixture/synthetic only). Added a deterministic,
no-lookahead signal replay layer and a research job that runs the full-period
backtest plus walk-forward test windows over `local_historical` CSV data.

Research-only: every result carries `research_only=True` and
`live_trading_approval=False`. No broker adapters, order plans, or promotion
state are touched.

## Implemented

- `quantpilot/packages/core/backtest/replay.py` — replays the same
  deterministic Level 1-2 snapshot classifier (`classify_fixture_bar`) over
  provider price history, one session at a time. Indicators per signal date
  come from `calculate_technical_indicators`, which only consumes bars on or
  before that date. An assumed position weight is tracked per symbol so
  exit/trim rules arm after buys. Optional `limit_buffer_bps` widens limit
  prices (buys up, sells down) for fill-sensitivity studies.
- `quantpilot/jobs/run_local_backtest.py` — CLI job: local providers →
  replay → `run_backtest` (full period + walk-forward test windows) →
  optional acceptance-threshold evaluation (skipped with an explicit note when
  no thresholds are supplied, since thresholds are a pending human input).
- Tests: 7 replay tests (including a **no-lookahead regression test**: signals
  on or before a cutoff are byte-identical after appending wild future bars —
  the guard `docs/stage_02_04_preparation_handoff.md` required before signal
  replay) and 2 job-level tests on synthetic CSV data.

## Real-data run (삼성전자/SK하이닉스/NAVER, 2025-06-02 → 2026-07-03, 798 bars)

Assumptions: fee 15 bps, slippage 5 bps, sell tax 20 bps, next-open-limit-touch
fills, initial cash 10,000,000.

| Run | Replayed signals | Filled | Blocked | Total return | Max DD | Sharpe* |
|---|---|---|---|---|---|---|
| `--limit-buffer-bps 0` (default) | 5 | 0 | 5 | 0.0% | 0.0% | 0.0 |
| `--limit-buffer-bps 50` | 5 | 2 | 3 | +5.32% | 2.56% | 1.64 |

*simplified Sharpe as defined by Stage 03 metrics.

Filled round trip (50 bps run): 005930 buy 2025-09-09 @ 71,835.9 → exit
(ma20 break) 2025-11-21 @ 97,751.1, realized PnL +417,462 KRW. The 000660 buy
on 2026-06-24 stayed blocked (`limit_not_touched`) even with the buffer.

## Findings (research observations, not fixes in this stage)

1. **Fill-model miss rate is structural.** With limit == signal-day close, both
   real buy signals were blocked by `limit_not_touched`: the strategy buys on
   volume-confirmed momentum, so the next bar tends to gap away from the
   close. Fill assumptions materially change results; `--limit-buffer-bps`
   now makes that sensitivity measurable.
2. **Exit leaves a residual position.** The 005930 exit sold 16.2 of 16.7 held
   shares, leaving ~0.5 shares (1.48% gross exposure) after a full exit
   signal. Engine computes sell quantity from target-weight notional at the
   signal price rather than liquidating held quantity. Candidate engine
   refinement for a future stage.
3. **Signal frequency is low on real data**: 5 actionable signals from 3
   large-cap symbols over 13 months. Fixture data was crafted to trigger
   setups; real-data strategy evaluation needs a wider universe and/or longer
   window before acceptance thresholds are meaningful.

## Validation

- `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp`
  → 288 collected: 287 passed, 1 skipped, 0 failures (junit-verified).
- Real-data job runs shown above; smoke unaffected.

## Safety

- No new network paths (job reads local CSVs only), no broker calls, no
  credentials, no flag changes. Live trading remains disabled.
