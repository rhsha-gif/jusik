# Local Historical Market Data (offline)

QuantPilot is **fixture-first**: by default it reads in-repo fixtures and never
touches the network or a broker. This document explains how to optionally point
the harness at **local CSV files** containing real historical data so signals can
be generated offline.

Local mode is **opt-in and fail-closed**. It activates only when you explicitly
set `DATA_MODE=local_historical` *and* provide a data directory. Any
misconfiguration raises an error rather than silently falling back to fixtures or
guessing.

## Safety

- No network calls, no broker calls, no credentials are read or required.
- `LIVE_TRADING_ENABLED`, `MARKET_ORDERS_ENABLED`, and broker mode are unaffected;
  live trading stays disabled. Local mode only changes **where market data comes
  from**, never how (or whether) orders are placed.
- Risk gates, the order state machine, idempotency, kill switches, and audit
  logging are untouched.

## 1. Create a data directory

Put two CSV files in a directory of your choice, e.g. `C:\quant-data\` or
`./my_local_data/`:

```
my_local_data/
  securities.csv   # universe metadata
  ohlcv.csv        # daily OHLCV time-series
```

A tiny working example lives at `quantpilot/tests/fixtures/local_data/`.

## 2. `securities.csv` schema

One row per symbol. Required columns: `symbol` (or `ticker`), `name`, `market`,
`sector`. Optional: a themes/tags column, an average-daily-value column, and
`data_ready`.

| Column | Required | Notes |
|---|---|---|
| `symbol` (or `ticker`) | yes | Ticker; upper-cased on load. Duplicates rejected. |
| `name` | yes | Display name. |
| `market` | yes | e.g. `US_STOCK`, `KR_STOCK`. |
| `sector` | yes | e.g. `technology`. |
| `themes` (or `tags`) | no | Pipe-delimited, e.g. `ai|semiconductor`. |
| `avg_daily_value` (or `average_daily_value`, `liquidity`) | no | Numeric liquidity proxy; defaults to `0`. |
| `data_ready` | no | `false`/`0`/`no`/`n` ⇒ not ready; anything else ⇒ ready (default). |

Example:

```csv
symbol,name,market,sector,themes,avg_daily_value,data_ready
AAA,Alpha AI Semiconductors,US_STOCK,technology,ai|semiconductor,10500000,true
BBB,Beta Cloud Platforms,US_STOCK,technology,ai|software,9180000,true
```

## 3. `ohlcv.csv` schema

One row per `(symbol, date)`. All seven columns are required.

| Column | Notes |
|---|---|
| `symbol` | Ticker; upper-cased on load. |
| `date` | **Plain session date** `YYYY-MM-DD`. Timezone/datetime strings (e.g. `...T00:00:00Z`) are rejected so session assumptions stay explicit. One bar per trading day. |
| `open` `high` `low` `close` | Numeric, must be positive; `high >= low` enforced. |
| `volume` | Numeric, must be non-negative. |

Duplicate `(symbol, date)` bars are rejected. Example:

```csv
symbol,date,open,high,low,close,volume
AAA,2026-06-01,99.5,100.5,99.0,100.0,100000
AAA,2026-06-02,100.0,101.5,99.8,101.0,101000
BBB,2026-06-01,100.5,101.0,99.5,100.0,90000
```

The provider derives the indicators the signal engine needs (`ma20`, `rsi`,
`volume_ratio`) from this raw series by reusing the same
`calculate_technical_indicators` code path the fixtures use — so local data is
classified by exactly the same deterministic engine.

## 4. Enable local mode

Set two environment variables before running the harness or API:

```powershell
$env:DATA_MODE = "local_historical"
$env:LOCAL_DATA_DIR = "C:\path\to\my_local_data"
```

```bash
export DATA_MODE=local_historical
export LOCAL_DATA_DIR=/path/to/my_local_data
```

Then build the harness from the environment:

```python
from quantpilot.packages.core.harness_service import HarnessService

service = HarnessService.from_environment()
signals = service.run_signals()  # generated from your local CSV data, offline
```

Or build the providers directly:

```python
from pathlib import Path
from quantpilot.packages.core.data.providers import build_providers
from quantpilot.packages.core.schemas import DataMode

security_provider, market_data_provider = build_providers(
    DataMode.local_historical, data_dir=Path("/path/to/my_local_data")
)
service = HarnessService(
    security_provider=security_provider,
    market_data_provider=market_data_provider,
)
```

## 5. Failure modes (all fail closed)

| Situation | Result |
|---|---|
| `DATA_MODE=local_historical` but no `LOCAL_DATA_DIR` / `data_dir` | `ProviderError: requires a data directory` |
| Missing CSV file | `ProviderError: local data file not found: ...` |
| Missing required column | `ProviderError: ... missing required column(s): ...` |
| Non-numeric price/volume | `ProviderError: ... is not a number: ...` |
| Bad or timezone-aware date | `ProviderError: invalid date ...` / `... plain session date ...` |
| Duplicate bar / duplicate symbol | `ProviderError: duplicate bar ...` / `duplicate symbol ...` |
| Symbol in `securities.csv` with no rows in `ohlcv.csv` | `ProviderError: missing OHLCV rows ...` |
| Unknown `DATA_MODE` value | `DataModeConfigError: Unsupported DATA_MODE ...` |

In the API layer these surface as HTTP `503` with a clear message; the default
(`DATA_MODE` unset) always resolves to the in-repo fixtures.

## 6. Defaults unchanged

With `DATA_MODE` unset (or `fixture`), the harness uses
`FixtureSecurityProvider` / `FixtureMarketDataProvider`, which wrap the existing
in-repo fixtures and produce byte-for-byte the same data as before. Existing
tests and smoke output are unaffected.
