# Live Trading Enablement Checklist

**Status: live trading is NOT implemented and NOT planned in this repository.** This checklist exists so that any future attempt to connect a real broker has a documented, human-gated path. Nothing below is satisfied today, and the code intentionally has no live broker mode (`BrokerMode` has no live value; `live_disabled` raises).

Every item requires a human owner and recorded evidence. An AI agent must never check these boxes.

## Hard prerequisites (all must be complete before writing any live-broker code)

1. [ ] Human decision, in writing, that live trading is wanted — including maximum capital at risk.
2. [ ] Legal/branch review of broker API terms and personal-trading regulations in the operator's jurisdiction.
3. [ ] Secret management designed (broker credentials never in repo, env files, logs, reports, or audit events) and reviewed.
4. [ ] A real-time market data source with documented staleness guarantees replacing fixture OHLCV.
5. [ ] Exchange calendar/halt handling replacing the simplified KRX window check.
6. [ ] Reconciliation: broker-reported positions/fills vs internal state, with alerting on divergence.
7. [ ] Independent human review of the entire order path: planner → risk gatekeeper → state machine → broker adapter.
8. [ ] Paper trading track record: a predefined evaluation window with the exact strategy registry intended for live, reviewed by a human.
9. [ ] Capital limits wired as deterministic checks (per-order, daily, total exposure) at values reviewed by a human.
10. [ ] Kill-switch drill performed: policy kill switch, `OPERATOR_KILL_SWITCH`, and process termination each verified to stop trading.
11. [ ] Incident runbook: who is paged, how positions are flattened manually, broker support contacts.
12. [ ] A new, separately reviewed `LIVE_TRADING_ENABLED` design — the current code treats `true` as a reason to refuse to run (`live_trading_flag_engaged`), and that behavior must remain until every item above is complete.

## Permanent rules even after enablement

- Live submission must keep every Level 5 gate (flags, policy promotion, version match, fresh risk checks, idempotency, loss limits, kill switches).
- LLM/RL output must still never construct or submit a broker order.
- Any risk-limit change still requires a new policy version plus explicit human confirmation.
