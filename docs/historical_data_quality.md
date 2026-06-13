# Historical Data Quality Gates

Stage05.5 adds a narrow quality layer for external historical daily bars before any Stage06 realtime work. It is read-only: it does not add realtime ingestion, broker account access, order APIs, or live-trading credentials.

## Scope

The quality gate runs after provider payloads are mapped into QuantPilot OHLCV rows and before those rows are exposed through `get_price_history()` or `get_bars()`. Blocking issues raise `ProviderError`, so external historical mode fails closed instead of silently falling back to fixtures or filling gaps.

Fixtures remain the default:

- `DATA_MODE=fixture`
- `BROKER_MODE=mock`
- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`

## Quality Rules

The gate emits explicit `HistoricalDataQualityIssue` entries. These blocking issues stop the provider:

- `missing_bar`: a requested symbol has no bar for an expected trading session.
- `stale_latest_bar`: the latest bar is older than the calendar-derived latest expected session.
- `duplicate_bar`: the provider returned more than one bar for the same symbol/session.
- `non_monotonic_dates`: provider bars for a symbol are neither chronological nor reverse-chronological.
- `invalid_ohlc`: prices are missing, non-numeric, non-positive, high is below low, or open/close fall outside low/high.
- `invalid_volume`: volume is zero, negative, missing, or non-numeric.
- `symbol_mismatch`: a row is missing a symbol or contains a symbol outside the requested set.

Provider-supplied warnings are preserved in the same report. They are visible in provenance and backtest summaries; they are not hidden or converted into synthetic bars.

## Calendar Behavior

`SimpleKrxCalendar` is intentionally minimal. It treats Monday through Friday as trading sessions, skips weekends, and skips caller-configured holidays.

This means:

- Weekends are not reported as missing bars.
- Dates listed in `EXTERNAL_HISTORICAL_HOLIDAYS` are not reported as missing bars.
- Weekday gaps that are not configured holidays are reported as `missing_bar`.
- Latest-bar freshness uses the latest trading session on or before the requested end date. For example, a Friday bar can be fresh for a Sunday end date, while a Tuesday bar is stale for a Wednesday trading-session end date.

The calendar is not a complete KRX holiday authority. Stage06 realtime should not proceed until the deployed environment has an explicitly reviewed holiday source or a maintained holiday configuration for the instruments being used.

## Provenance And Backtests

External historical providers expose:

- `get_provenance()`: provider name, data mode, fetch timestamp, market, symbol window, retry/rate metadata, and quality report.
- `get_data_quality()`: the standalone quality report.

Backtest `input_summary` copies both into:

- `data_provenance`
- `data_quality`

This keeps research output auditable without changing the backtest result schema.

## Manual KIS Verification

KIS historical verification remains manual and opt-in:

```powershell
$env:RUN_KIS_MANUAL_INTEGRATION="1"
$env:DATA_MODE="external_historical"
$env:EXTERNAL_HISTORICAL_PROVIDER="kis"
$env:EXTERNAL_HISTORICAL_MARKET="KR_STOCK"
$env:EXTERNAL_HISTORICAL_SYMBOLS="005930"
$env:EXTERNAL_HISTORICAL_START="2026-01-05"
$env:EXTERNAL_HISTORICAL_END="2026-01-09"
$env:EXTERNAL_HISTORICAL_HOLIDAYS=""
python -m pytest quantpilot/tests/integration/test_kis_historical_manual.py -q
```

The test is skipped unless `RUN_KIS_MANUAL_INTEGRATION=1` is set. Unit tests use fake clients only and do not require KIS credentials or internet access.

## Stage06 Readiness

Stage06 realtime should wait until the historical quality gate passes for the intended market, symbol set, and date window. Realtime adds stricter freshness, session-state, and reconciliation problems; entering it with missing historical bars, stale latest bars, duplicate sessions, or invalid OHLCV would make downstream signal, backtest, and operator decisions non-auditable.
