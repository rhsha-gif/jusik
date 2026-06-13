# QuantPilot Operator Pre-Harness

Fixture-only operating harness for QuantPilot Operator. Live broker trading is not implemented or enabled.

## Safe Defaults

```text
LIVE_TRADING_ENABLED=false
GUARDED_AUTOPILOT_ENABLED=false
FULLY_AUTOMATED_OPERATOR_ENABLED=false
BROKER_MODE=mock
DEFAULT_ORDER_TYPE=limit
MARKET_ORDERS_ENABLED=false
DATA_MODE=fixture
```

## Commands

`make` is not available in the verified Windows environment, so use these equivalents:

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
python -m uvicorn quantpilot.services.api.main:app --reload
```

When pytest temporary-directory permissions fail on Windows, use the same
workspace-local temp directory used by the hardening checks:

```powershell
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp
```

Compatible systems can also use:

```powershell
make test
make smoke
make api
```
