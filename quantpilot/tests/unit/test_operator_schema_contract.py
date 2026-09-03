from quantpilot.services.api.main import app


def test_operator_openapi_requires_safety_evidence_lists() -> None:
    schemas = app.openapi()["components"]["schemas"]

    assert {"submitted_order_plan_ids", "blocked_order_plan_ids"} <= set(
        schemas["OperatorRunResult"]["required"]
    )
    assert {"report_id", "broker_order_ids", "risk_check_ids", "safety_flags"} <= set(
        schemas["OperatorReport"]["required"]
    )
    assert "decision_id" in schemas["OperatorDecision"]["required"]
