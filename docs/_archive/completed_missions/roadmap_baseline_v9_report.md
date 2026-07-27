# QuantPilot Roadmap Baseline v9 Report

## Status

The completed KIS paper managed-order kill v1 branch is now the isolated
development baseline for the staged upgrade roadmap. The original `main`
worktree was not modified because it contains user-owned uncommitted changes.

- Baseline branch: `codex/qp-roadmap-baseline-v9`
- Baseline source: `codex/qp-paper-kill-v1-core` at `216ff22`
- SQLite schema: v9
- Live trading enabled: **no**
- Validation broker: **mock**
- Market orders enabled: **no**
- Real KIS network calls: **none**

## Verified safety behavior

- The durable paper kill fence and cancel journal are present.
- Kill states block new paper preparation and final broker POST authority.
- Cancel claims permit at most one external attempt; ambiguous outcomes are
  reconciled and never automatically reposted.
- Manual/external orders and position flattening remain outside the automatic
  kill scope.
- Kill release does not re-arm strategies, policy authority, or autonomy flags.
- The default environment blocks the kill CLI with `paper_kill_disabled`.

## Verification

```text
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp
819 passed, 2 skipped in 15.39s

python -m quantpilot.jobs.run_smoke
broker=mock
live_trading_enabled=false
operator.status=blocked
operator.fallback=level5_flag_disabled

python -m quantpilot.jobs.run_kis_paper_kill engage
{"status":"blocked","reason_code":"paper_kill_disabled"}
```

## Remaining gate

The inferred KIS paper cancelable-order inquiry TR `VTTC0084R` still requires
explicit manual validation with a separate paper account before operational
kill use. That external gate does not authorize live trading and does not block
fixture/fake-client development of the next atomic risk-reservation mission.
