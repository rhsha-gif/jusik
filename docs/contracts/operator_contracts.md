# Operator Contracts

These contracts define Level 5 surfaces without requiring a database implementation. Use Pydantic models if implemented in Python. Field names should remain stable unless this document and tests are updated together.

## OperatorRunRequest

```python
class OperatorRunRequest(BaseModel):
    user_id: str = "fixture-user"
    policy_id: str
    requested_policy_version: int
    run_mode: Literal["dry_run", "mock_submit", "paper_submit"] = "dry_run"
    requested_at: datetime
    idempotency_key: str
```

The request starts one bounded operator cycle. `run_mode` must never imply live trading.

## OperatorRunResult

```python
class OperatorRunResult(BaseModel):
    run_id: str
    status: Literal["completed", "blocked", "fallback", "failed"]
    submitted_order_plan_ids: list[str]
    blocked_order_plan_ids: list[str]
    fallback: FallbackDecision | None
    report: OperatorReport
```

The result is a summary for API callers. Detailed evidence belongs in `OperatorReport`.

## StrategyRegistryEntry

```python
class StrategyRegistryEntry(BaseModel):
    strategy_id: str
    version: str
    spec_hash: str | None = None
    status: Literal["draft", "validated_l3", "validated_l4", "validated_l5", "disabled", "revoked"]
    allowed_execution_levels: list[Literal["level_3", "level_4", "level_5", "guarded_autopilot", "fully_automated"]]
    priority: int = 100
    max_policy_version: int | None = None
    min_policy_version: int | None = None
    disabled_reason: str | None = None
```

Status and allowed levels must both permit Level 5 before selection. Any entry
with execution authority must also bind to matching lifecycle evidence using the
same `strategy_id`, `version`, and `spec_hash`; missing evidence fails closed.

## StrategySelectionDecision

```python
class StrategySelectionDecision(BaseModel):
    selected_strategy_id: str | None
    selected_version: str | None
    eligible_strategy_ids: list[str]
    rejected: dict[str, str]
    reason: str
```

This records why a strategy was selected or why no strategy was eligible.

## OperatorDecision

```python
class OperatorDecision(BaseModel):
    decision_id: str
    run_id: str
    policy_id: str
    policy_version: int
    strategy_id: str | None
    order_plan_id: str | None
    action: Literal["submit", "block", "fallback", "noop"]
    reason: str
    risk_check_id: str | None
    created_at: datetime
```

Every nontrivial branch in the Level 5 loop should have a decision entry.

## FallbackDecision

```python
class FallbackDecision(BaseModel):
    from_level: Literal[5]
    to_level: Literal[4, 3, 2, 0]
    reason_code: str
    detail: str
    order_submission_enabled: bool = False
```

Fallbacks must be deterministic and auditable. `to_level=0` means no-op.

## PolicyReviewRequest

```python
class PolicyReviewRequest(BaseModel):
    policy_id: str
    current_version: int
    requested_version: int
    reason: str
    blocks_automatic_submission: bool = True
```

Use this when the operator cannot safely continue due to policy drift or missing approval.

## PolicyVersionChange

```python
class PolicyVersionChange(BaseModel):
    policy_id: str
    previous_version: int
    next_version: int
    changed_fields: list[str]
    changed_at: datetime
    changed_by: str
    requires_review: bool
```

Material risk, broker, or authority changes must require review.

## OperatorReport

```python
class OperatorReport(BaseModel):
    report_id: str
    run_id: str
    user_id: str
    policy_id: str
    policy_version: int
    started_at: datetime
    completed_at: datetime
    status: Literal["completed", "blocked", "fallback", "failed"]
    strategy_selection: StrategySelectionDecision
    decisions: list[OperatorDecision]
    fallback: FallbackDecision | None
    order_plan_ids: list[str]
    broker_order_ids: list[str]
    risk_check_ids: list[str]
    safety_flags: dict[str, bool | str]
    live_trading_enabled: bool = False
    audit_event_count: int
```

Reports are evidence. Do not omit blocked decisions just because no order was submitted.
