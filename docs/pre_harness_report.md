# QuantPilot Operator Pre-Harness Report

Date: 2026-06-12

## 1. Files Created or Changed

- Project scaffold: `pyproject.toml`, `README.md`, `Makefile`, `.env.example`, `.gitignore`.
- Core schemas and orchestration: `quantpilot/packages/core/schemas.py`, `quantpilot/packages/core/harness_service.py`.
- Policy, strategy, signal, portfolio, risk, execution, analyst, RL, and reporting modules under `quantpilot/packages/core/`.
- Broker boundary: `quantpilot/packages/brokers/base.py`, `mock_broker.py`, `paper_broker.py`.
- In-memory repositories and audit recorder under `quantpilot/packages/db/`.
- FastAPI service and routers under `quantpilot/services/api/`.
- CLI smoke command: `quantpilot/jobs/run_smoke.py`.
- Fixtures: `quantpilot/docs/strategy_specs/pullback_trend_v1.yaml`, `quantpilot/tests/fixtures/ohlcv.json`.
- Tests: `quantpilot/tests/unit/test_pre_harness.py`, `quantpilot/tests/integration/test_smoke.py`.

## 2. Implemented Schemas

Implemented Pydantic schemas:

- `UserPolicy`
- `StrategyRecipe`
- `Signal`
- `PortfolioPlan`
- `OrderIntent`
- `RiskCheck`
- `OrderPlan`
- `BrokerOrder`
- `Fill`
- `PortfolioSnapshot`
- `AuditLogEvent`
- `OperationReport`

Implemented enums:

- `ExecutionMode`
- `SignalAction`
- `OrderStatus`
- `BrokerMode`
- `OrderType`

## 3. Safety Invariants Implemented

- Live broker execution is not implemented; `BrokerMode.live_disabled` is rejected by the risk gate.
- Default broker mode is `mock`.
- `PaperBroker` produces simulated fills only and tracks zero live API calls.
- Market orders are blocked by default through `MARKET_ORDERS_ENABLED=false`.
- Submission requires a successful risk check.
- `approval_required` mode requires explicit order approval before submission.
- `guarded_autopilot` and `fully_automated` exist in schemas but are not executable pre-harness modes.
- Every `OrderPlan` requires an idempotency key.
- Order state transitions emit audit events.
- Fable5 recipes cannot directly submit or approve orders.
- RL output schema only allows `target_weight_delta` and `strategy_selection`.
- No secrets are stored or logged by the harness.

## 4. API Routes Implemented

- `GET /api/health`
- `POST /api/harness/run-smoke`
- `POST /api/policies/parse`
- `POST /api/policies/confirm`
- `POST /api/signals/run`
- `POST /api/portfolio/plan`
- `POST /api/orders/plan`
- `GET /api/orders/proposed`
- `POST /api/orders/{order_plan_id}/approve`
- `POST /api/orders/{order_plan_id}/submit`
- `GET /api/orders/{order_plan_id}/status`
- `POST /api/reports/daily`

## 5. Tests Added

Added 18 tests covering:

- Invalid policy weights.
- Invalid loss limits.
- Strategy recipe loading.
- All signal actions from fixtures.
- Portfolio planner max position, min cash, and order-size limits.
- Risk check required before submit.
- Approval required before submit.
- Duplicate idempotency key rejection.
- Monthly loss pause and stop checks.
- Market orders blocked by default.
- Stale quote rejection.
- Mock broker account/order/fill flow.
- Paper broker simulated-only flow.
- Audit logs on transitions.
- End-to-end policy-to-report smoke flow.
- FastAPI smoke route.
- Fable5 direct submission block.

## 6. Commands Run and Results

```powershell
python -m pytest quantpilot/tests
```

Result: `18 passed in 3.30s`

```powershell
python -m quantpilot.jobs.run_smoke
```

Result: PASS. Summary included `broker=mock`, `execution_mode=approval_required`, `signals=7`, `fills=3`, all orders `filled`, and `live_trading_enabled=false`.

## 7. Known Limitations

- Repositories are in-memory only and reset per process.
- Strategy and signal logic are deterministic fixture stubs, not production analyst logic.
- Portfolio snapshots are fixture-based and are not persisted as a separate repository.
- Paper fills are simulated with fixed fixture prices.
- API request models are intentionally minimal.
- `make` is not installed in the verified Windows environment; equivalent commands are documented in `README.md`.

## 8. Next Recommended Step

Proceed to Level 1-2 implementation on top of this harness:

`03_CODEX_LEVEL_1_2_ON_HARNESS_IMPLEMENTATION_PROMPT.md`
