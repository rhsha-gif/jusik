# QuantPilot Operator Pre-Harness

Fixture-only operating harness for QuantPilot Operator. Live broker trading is not implemented or enabled.

## Safe Defaults

```text
LIVE_TRADING_ENABLED=false
BROKER_MODE=mock
DEFAULT_ORDER_TYPE=limit
MARKET_ORDERS_ENABLED=false
```

## Commands

`make` is not available in the verified Windows environment, so use these equivalents:

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
python -m uvicorn quantpilot.services.api.main:app --reload
```

Compatible systems can also use:

```powershell
make test
make smoke
make api
```
