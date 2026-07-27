# Step 07 Candidate Ranking Report

## Implemented

- Added strict Pydantic ranking contracts:
  - `CandidateScore`
  - `RankingExplanation`
  - `RankedCandidate`
- Added `CandidateRankingEngine` for deterministic component scoring:
  - theme fit
  - sector fit
  - liquidity capacity
  - data quality
  - volatility
  - portfolio correlation
  - existing exposure
  - fundamental or valuation availability
- Added neutral degraded handling for unavailable ranking inputs.
- Added final candidate cap with `candidate_cap_exceeded` exclusion reasons.
- Added `build_ranked_candidate_universe` as an explicit builder adapter.
- Preserved legacy `build_candidate_universe` output and default behavior.

## Data Assumptions

- Ranking consumes fixture/local provider security metadata only.
- Missing volatility, correlation, portfolio snapshot, or fundamental metadata scores neutral at `50.0` and is disclosed in `RankingExplanation.unavailable_data`.
- Existing filter exclusions remain authoritative before selection:
  - `policy_blocklist`
  - `fixture_blocked`
  - `liquidity_below_minimum`
  - `data_unavailable`
  - `theme_mismatch`

## Safety Invariants

- Live trading enabled: no.
- Broker mode used for validation: mock.
- No broker connector, credential UI, live order path, or market-order enablement was added.
- Ranking is a research/universe-selection path only and does not submit or approve orders.

## Validation

- `python -m pytest quantpilot/tests/core/universe -q`
  - Result: `6 passed`
- `python -m pytest quantpilot/tests`
  - Initial run hit a Windows temp-directory permission error under `C:\Users\goyan\AppData\Local\Temp\pytest-of-goyan`.
  - Rerun with workspace-local `TMP`/`TEMP`: `257 passed, 1 skipped`
- `python -m quantpilot.jobs.run_smoke`
  - Result: passed
  - Broker: `mock`
  - Execution mode: `approval_required`
  - Live trading enabled: `false`
  - Operator fallback: `level5_flag_disabled`

## Known Limitations

- Component weights are deterministic defaults, not signal-calibrated weights.
- Volatility, correlation, and fundamental scores use provided metadata only; no live or external provider lookup is performed.
- Ranking is not an ML model and does not change downstream trading authority.

## Next Recommended Step

- Keep the next stage separate: feed ranked candidates into signal generation or research presentation only after adding focused tests that prove no execution authority is derived from ranking.

