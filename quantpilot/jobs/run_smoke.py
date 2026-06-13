from __future__ import annotations

import json

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.operator.schemas import OperatorRunRequest
from quantpilot.packages.core.operator.service import OperatorService


def main() -> int:
    harness = HarnessService.from_environment()
    summary = harness.run_smoke()

    # Level 5 smoke: with default flags the operator must refuse to run and report a
    # deterministic no-op fallback. This exercises the gate chain without submission.
    operator = OperatorService(harness)
    operator_result = operator.run_once(
        OperatorRunRequest(
            policy_id=str(summary["policy_id"]),
            requested_policy_version=1,
            run_mode="dry_run",
            idempotency_key="smoke-operator-run",
        )
    )
    summary["operator"] = {
        "status": operator_result.status,
        "fallback": operator_result.fallback.reason_code if operator_result.fallback else None,
        "submitted_order_plan_ids": operator_result.submitted_order_plan_ids,
        "live_trading_enabled": operator_result.report.live_trading_enabled,
    }

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
