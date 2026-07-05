# QuantPilot Operator Pre-Harness

Fixture-only operating harness for QuantPilot Operator. Live broker trading is not implemented or enabled.

Level 1-2 has two separate paths:

- `POST /api/level-1-2/run` keeps the research/signal/rebalance suggestion contract and never creates orders.
- `POST /api/level-1-2/mock-execute` converts fixture timing signals into order plans and submits them only through `MockBroker`.

Approval tickets for future paper/live-candidate stages are available under
`/api/execution/approval-tickets/*`. `live_trading_candidate` tickets can be
approved by a user, but the system blocks before broker submission because no
live broker adapter exists.

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
