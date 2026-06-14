# Step 04 Provider-Bound Signals Report

## Status

Complete.

## Implemented

- Added provider-bound market data contracts under `quantpilot/packages/core/marketdata`.
- Added `OHLCVProvider`, `QuoteProvider`, and interface-only `L2Provider`.
- Added fixture and fake OHLCV/quote providers.
- Added serializable provider metadata models: `ProviderStatus`, `MarketDataQuality`, and `SignalSet`.
- Added `generate_provider_bound_signals(...)` while preserving the existing `generate_signals(...)`, `load_fixture_ohlcv(...)`, and fixture behavior.
- Moved Level 5 operator signal recording off direct fixture reads and onto injected providers.
- Added fail-closed behavior: unavailable or stale provider status records blocked signals and prevents Level 5 order planning.

## Data Assumptions

- Default data mode remains `fixture`.
- Fixture and fake providers are deterministic and local-only.
- Quote providers in this step derive safe reference quotes from fixture/local bars only.
- No realtime market data, live provider, broker credential, or live broker path was added.

## Safety Invariants

- `LIVE_TRADING_ENABLED=false` remains the default.
- `GUARDED_AUTOPILOT_ENABLED=false` remains the default.
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false` remains the default.
- `MARKET_ORDERS_ENABLED=false` remains the default.
- `BROKER_MODE=mock` remains the default.
- Provider unavailable/stale status cannot produce `buy_ready`.
- Level 5 degraded signal data exits with no order plan and no broker submission.

## Tests

- `python -m pytest quantpilot/tests/unit/test_marketdata_provider_bound_signals.py quantpilot/tests/unit/test_level5_signal_provider_path.py -q`
- `python -m pytest quantpilot/tests/unit/test_providers.py quantpilot/tests/unit/test_level_1_2.py quantpilot/tests/integration/test_level5_operator_run_once.py quantpilot/tests/unit/test_level5_signal_provider_path.py quantpilot/tests/unit/test_marketdata_provider_bound_signals.py -q`
- `python -m pytest quantpilot/tests`
- `python -m quantpilot.jobs.run_smoke`

## Results

- Full backend suite: 232 passed, 1 skipped.
- Smoke: broker `mock`, live trading `false`, Level 5 operator blocked by default with `level5_flag_disabled`.

## Known Limitations

- L2 is an interface only in this step.
- Quote provider values are reference quotes derived from fixture/local bars, not realtime quotes.
- Provider metadata is returned from `SignalSet`; existing `Signal` objects remain unchanged for compatibility.

## Next Recommended Step

Add explicit provider-status display or audit/report surfacing if downstream UI/API consumers need to inspect degraded market data without calling the signal service directly.
