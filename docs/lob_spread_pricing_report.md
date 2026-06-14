# LOB Spread Pricing Implementation Report

## Scope

Implemented a deterministic, fixture-safe limit order book spread pricing module
based on `C:\Users\goyan\Downloads\lob_spread_algorithms_en.md`.

This is a future live-trading-candidate building block only. It does not add
broker credentials, live broker submission, live market-data calls, or any
default live-trading authority.

## Implemented

- Added typed L2 book and instrument microstructure DTOs:
  - `BookLevel`
  - `L2OrderBook`
  - `InstrumentMicrostructure`
  - `LOBProfile`
  - `LOBFeatures`
  - `LimitPriceDecision`
- Added deterministic LOB checks:
  - sequence gap
  - stale book
  - invalid BBO
  - locked/crossed book
  - abnormal spread
  - HALT/VI/LULD/non-trading session block
  - KRX-style `CONTINUOUS` and U.S. `REGULAR`/`EXTENDED` session handling
- Added LOB features:
  - mid price
  - spread ticks
  - weighted multi-level imbalance
  - microprice
  - VAMP
  - weighted-depth price
- Added fair-price and passive limit-price logic:
  - common equity fair price from microprice and VAMP
  - Avellaneda-Stoikov-style half-spread in ticks
  - maker-only/post-only crossing prevention
  - expected-value gate
  - tick rounding
  - price-limit checks
- Integrated `build_portfolio_plan()` so provided L2 books can override the
  previous close-price limit price. If an unsafe L2 book is provided, new order
  intent creation is blocked for that symbol.
- If an L2 book is provided without matching instrument microstructure
  (`tick_size`, session, price-limit metadata), the planner fails closed and
  blocks new order intent creation for that symbol.

## Files

- `quantpilot/packages/core/execution/lob_spread.py`
- `quantpilot/packages/core/portfolio/planner.py`
- `quantpilot/tests/unit/test_lob_spread_pricing.py`

## Safety Invariants

- `LIVE_TRADING_ENABLED=false` preserved.
- `GUARDED_AUTOPILOT_ENABLED=false` preserved.
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false` preserved.
- `MARKET_ORDERS_ENABLED=false` preserved.
- `BROKER_MODE=mock` preserved.
- No credential UI or secret handling added.
- No live broker API path added.
- Existing mock/paper-only submission gates remain unchanged.

## Validation

- `python -m pytest quantpilot/tests/unit/test_lob_spread_pricing.py -q -p no:cacheprovider --basetemp=.pytest_tmp`
- `python -m pytest quantpilot/tests/unit/test_pre_harness.py quantpilot/tests/unit/test_level3_proposals.py -q -p no:cacheprovider --basetemp=.pytest_tmp`
- `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp`
- `python -m quantpilot.jobs.run_smoke`

## Known Limitations

- No live L2 market-data provider is added.
- No order cancel/replace service is added.
- ETF NAV anchoring, U.S. NBBO venue routing, OFI windows, markout tuning, and
  queue-position simulation remain future stages.
- Current runtime still uses fixture or historical bar data unless explicit L2
  books are injected into the planner.
