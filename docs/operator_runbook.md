# Operator Runbook (Level 5)

This runbook explains how to run, observe, and stop the QuantPilot Level 5 fully automated operator. The operator is **disabled by default** and only ever talks to MockBroker or PaperBroker. In plain terms: with default settings, running the operator does nothing, and no real money is ever involved.

## What a run does

One operator run is a single bounded decision cycle:

1. Checks every safety gate (feature flag, kill switches, broker mode, policy version, policy promotion).
2. Selects one strategy from the approved registry (`validated_l5` only).
3. Syncs the portfolio snapshot from the mock/paper broker.
4. Generates signals with the selected strategy's recipe only.
5. Builds a portfolio plan and Level 3-style order proposals (each with a deterministic risk check and idempotency key).
6. In `mock_submit`/`paper_submit` mode, re-authorizes each proposal (`authorize_level5`, including a fresh risk check) and submits through the existing order state machine. In `dry_run` mode nothing is submitted.
7. Records an `OperatorReport` and audit events for every material branch.

## How to run it

Dry run via API (server must be running):

```text
POST /api/operator/run-once
{
  "policy_id": "<policy id>",
  "requested_policy_version": <current policy version>,
  "run_mode": "dry_run",
  "idempotency_key": "<unique key per intended run>"
}
```

- `run_mode` is one of `dry_run`, `mock_submit`, `paper_submit`. There is no live mode.
- `requested_policy_version` must equal the active policy version, otherwise the run blocks with `policy_review_required`.
- Reusing an `idempotency_key` replays the recorded result instead of creating new orders (unless a kill switch engaged since; then the run re-blocks).

Status and latest report:

```text
GET /api/operator/status
GET /api/operator/reports/latest
```

Smoke check from the shell:

```powershell
python -m quantpilot.jobs.run_smoke
```

The `operator` section of the smoke output must show `"status": "blocked"`, `"fallback": "level5_flag_disabled"`, and `"live_trading_enabled": false` with default flags.

## What must be true before the operator can submit (mock/paper)

Every one of these, in order:

1. `FULLY_AUTOMATED_OPERATOR_ENABLED=true` (env) or `fully_automated_operator_enabled: true` on the policy (a material, confirmation-gated field).
2. An active policy exists for the requested `policy_id`.
3. `LIVE_TRADING_ENABLED` is **false** (if it is ever true, the operator refuses to run at all).
4. No kill switch: `policy.kill_switch_engaged=false` and `OPERATOR_KILL_SWITCH` unset/false.
5. Policy broker is `mock` or `paper`, and matches the run mode.
6. `requested_policy_version` matches the active policy version.
7. Policy promoted: `authority_level=5` **and** `execution_mode=fully_automated`.
8. A `validated_l5` registry strategy is eligible (with a loadable recipe).
9. Monthly loss stop not triggered (checked after the broker snapshot sync).
10. Per order: fresh quote, KRX continuous session, allowed order type, monthly loss pause (buys), no conflicting unfilled order, new idempotency key, and a passing fresh deterministic risk check — re-checked again inside `submit_order_plan`.

If any gate fails, the run ends with a deterministic fallback (see `docs/operator_fallback_matrix.md`) and a report. The operator never retries on its own.

## How to stop it

- **Kill switch (policy):** `POST /api/autopilot/kill-switch` — stops guarded autopilot and Level 5; demotes the policy to approval-required.
- **Kill switch (environment):** set `OPERATOR_KILL_SWITCH=true` — blocks every operator run, including idempotent replays.
- **Feature flag:** set `FULLY_AUTOMATED_OPERATOR_ENABLED=false` (default) — operator runs become no-ops.
- A broker failure during submission pauses the harness (`autopilot_paused=true`) automatically; resume requires `POST /api/autopilot/guarded/resume` after investigation.

## Reading a report

`GET /api/operator/reports/latest` returns the structured report plus a plain-text rendering (generated deterministically, no LLM required). Check, in order:

1. `status` — `completed`, `fallback`, `blocked`, or `failed`.
2. `fallback.reason_code` — why the run degraded, if it did.
3. `strategy_selection` — which strategy ran and why others were rejected.
4. `decisions` — one entry per material branch (submit/block/fallback/noop with reason).
5. `risk_check_ids` and `broker_order_ids` — evidence for each submission.
6. `safety_flags` — flag states observed at run time. Both the report's top-level `live_trading_enabled` field and the `LIVE_TRADING_ENABLED` entry inside `safety_flags` must read `false`.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `level5_flag_disabled` | default flags | expected; enable only after the safety checklist |
| `policy_review_required` | policy version drift | re-read the active policy, confirm pending updates, rerun with the current version |
| `policy_not_promoted` | authority < 5 or mode != fully_automated | promote via policy versioning flow with explicit confirmation |
| `no_level5_strategy_eligible` | registry has no `validated_l5` entry | promote a strategy per `docs/operator_strategy_promotion_policy.md` |
| `stale_market_data` | quotes older than `stale_quote_max_age_seconds` | refresh data; do not widen the staleness limit casually |
| `broker_failure` + paused harness | broker adapter raised during submit | inspect audit events (`broker_health_failed`), fix, then resume explicitly |
