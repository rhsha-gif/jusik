# QuantPilot Codex Setup

This directory contains local setup notes for safe Codex work on QuantPilot.

## Safe Commands

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
```

On systems with `make`:

```powershell
make test
make smoke
```

## Safety Defaults

Do not change defaults that keep live trading disabled:

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `BROKER_MODE=mock`
