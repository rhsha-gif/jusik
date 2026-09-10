# marketdata

Research-only daily OHLCV fetcher with a self-contained candlestick chart.
This directory is completely separate from QuantPilot's broker, credential,
and order paths — it reads public quotation APIs and writes local files, and
nothing here can place or influence an order.

## Usage

```powershell
python -m marketdata.fetch --source upbit --symbol KRW-BTC
```

Outputs (both regenerable, both git-ignored):

- `marketdata/data/upbit_KRW-BTC_1d.csv` — `symbol,date,open,high,low,close,volume`,
  same schema as `local_data/ohlcv.csv`
- `marketdata/out/upbit_KRW-BTC_1d.html` — open in a browser; wheel to zoom,
  drag to pan, crosshair tooltip, log-scale toggle. Zero external resources.

## Adding a new source

1. Implement the `DailySource` protocol (`marketdata/types.py`) in a new file
   under `marketdata/sources/` — return `Bar` dicts sorted by date, no
   duplicates, and raise instead of returning a partial history.
2. Register it in `SOURCES` in `marketdata/fetch.py` (one line).
3. Call it: `python -m marketdata.fetch --source <name> --symbol <symbol>`.

The CSV writer and chart consume only the `Bar` contract, so they need no
changes for a new asset class.

## Extension candidates

| Asset class | Endpoint | Existing implementation to borrow |
|---|---|---|
| US equities / ETF | Yahoo chart API | `experiments/swing_overlay/fetch_data.py:47` (working fetch) |
| Korean equities | pykrx | `quantpilot/jobs/fetch_krx_local_data.py` |
| Crypto in USD | Binance klines | none yet — same paging shape as Upbit |
