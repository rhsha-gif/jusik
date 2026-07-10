from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from quantpilot.jobs.run_kis_paper_session import (
    KisPaperSessionConfig,
    KisPaperSessionRuntime,
    PaperSessionError,
    _completed_pullback_bars,
    _operator_request,
    build_runtime,
    execute_runtime,
    load_explicit_paper_policy,
    load_explicit_paper_registry,
    main,
    paper_session_gate_reason,
)
from quantpilot.packages.core.execution.paper_reconciliation import (
    PaperReconciliationResult,
)
from quantpilot.packages.core.execution.paper_reconciliation_apply import (
    PaperReconciliationApplyResult,
)
from quantpilot.packages.core.kis_paper import KisBalanceResult, KisBalanceSummary
from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    PortfolioSnapshot,
    UserPolicy,
)
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.strategies.promotion import (
    PROMOTION_CONFIRMATION,
    PromotionEvidence,
    StrategyLifecycleRecord,
    StrategyLifecycleStatus,
    StrategyPromotionService,
    compute_spec_hash,
)
from quantpilot.packages.core.strategies.registry import (
    StrategyRegistry,
    StrategyRegistryEntry,
)
from quantpilot.packages.db.repositories import RepositoryRegistry


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)


def _enabled_environment(tmp_path) -> dict[str, str]:
    return {
        "KIS_PAPER_SESSION_ENABLED": "true",
        "KIS_PAPER_ORDER_SUBMISSION_ENABLED": "true",
        "FULLY_AUTOMATED_OPERATOR_ENABLED": "true",
        "LIVE_TRADING_ENABLED": "false",
        "MARKET_ORDERS_ENABLED": "false",
        "GUARDED_AUTOPILOT_ENABLED": "false",
        "BROKER_MODE": "paper",
        "DATA_MODE": "local_historical",
        "KIS_PAPER_STATE_DB": str((tmp_path / "paper.sqlite3").resolve()),
        "KIS_PAPER_POLICY_FILE": str((tmp_path / "policy.json").resolve()),
        "KIS_PAPER_REGISTRY_FILE": str((tmp_path / "registry.json").resolve()),
        "KIS_PAPER_APPROVED_BUSINESS_DATE": "2026-07-10",
        "KIS_PAPER_APP_KEY": "secret-app-key",
        "KIS_PAPER_APP_SECRET": "secret-app-secret",
        "KIS_PAPER_ACCOUNT_NUMBER": "12345678",
        "KIS_PAPER_PRODUCT_CODE": "01",
        "KIS_PAPER_ACCESS_TOKEN": "secret-access-token",
    }


def _policy() -> UserPolicy:
    return UserPolicy(
        policy_id="paper-policy",
        user_id="paper-user",
        broker=BrokerMode.paper,
        authority_level=5,
        execution_mode=ExecutionMode.fully_automated,
        fully_automated_operator_enabled=True,
    )


def _balance() -> KisBalanceResult:
    from decimal import Decimal

    return KisBalanceResult(
        positions=(),
        summary=KisBalanceSummary(
            deposit_amount=Decimal("1000000"),
            next_day_settlement_amount=Decimal("1000000"),
            total_purchase_amount=Decimal("0"),
            total_evaluation_amount=Decimal("0"),
            net_asset_amount=Decimal("1000000"),
            evaluation_profit_loss=Decimal("0"),
        ),
        pages_fetched=1,
    )


def _registry_payload() -> dict[str, object]:
    recipe = load_strategy_recipe("pullback_trend_v2")
    spec_hash = compute_spec_hash(recipe)
    entry = StrategyRegistryEntry(
        strategy_id=recipe.strategy_id,
        version=recipe.version,
        spec_hash=spec_hash,
        status="validated_l5",
        allowed_execution_levels=["level_5", "fully_automated"],
    )
    lifecycle_service = StrategyPromotionService()
    lifecycle_service.register_draft(
        strategy_id=recipe.strategy_id,
        version=recipe.version,
        spec_hash=spec_hash,
    )
    for evidence in (
        PromotionEvidence(
            kind="backtest_result",
            reference="backtest-evidence",
            summary="backtest evidence",
            recorded_by="human-reviewer",
        ),
        PromotionEvidence(
            kind="paper_track_record",
            reference="paper-evidence",
            summary="paper evidence",
            recorded_by="human-reviewer",
        ),
        PromotionEvidence(
            kind="risk_review",
            reference="risk-evidence",
            summary="risk evidence",
            recorded_by="human-reviewer",
        ),
    ):
        lifecycle_service.attach_evidence(
            strategy_id=recipe.strategy_id,
            version=recipe.version,
            evidence=evidence,
        )
    for _ in range(4):
        lifecycle_service.promote(
            strategy_id=recipe.strategy_id,
            version=recipe.version,
            confirmation=PROMOTION_CONFIRMATION,
            confirmed_by="human-reviewer",
        )
    lifecycle = lifecycle_service.require(recipe.strategy_id)
    return {
        "entries": [entry.model_dump(mode="json")],
        "lifecycle_records": [lifecycle.model_dump(mode="json")],
    }


def _registry() -> StrategyRegistry:
    payload = _registry_payload()
    return StrategyRegistry(
        [StrategyRegistryEntry.model_validate(payload["entries"][0])],  # type: ignore[index]
        lifecycle_records=[
            StrategyLifecycleRecord.model_validate(
                payload["lifecycle_records"][0]  # type: ignore[index]
            )
        ],
    )


def test_default_job_is_disabled_before_any_configuration_or_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setenv("KIS_PAPER_SESSION_ENABLED", "false")

    exit_code = main()

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "blocked",
        "reason_code": "paper_session_disabled",
    }


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        (
            {"KIS_PAPER_ORDER_SUBMISSION_ENABLED": "false"},
            "paper_order_submission_gate_disabled",
        ),
        ({"LIVE_TRADING_ENABLED": "true"}, "live_trading_flag_engaged"),
        ({"MARKET_ORDERS_ENABLED": "true"}, "market_orders_flag_engaged"),
        ({"BROKER_MODE": "mock"}, "paper_broker_mode_required"),
        ({"DATA_MODE": "fixture"}, "paper_historical_data_mode_required"),
        (
            {"DATA_MODE": "external_historical"},
            "paper_external_historical_origin_not_hardened",
        ),
    ],
)
def test_each_external_paper_gate_fails_closed(
    tmp_path,
    update: dict[str, str],
    reason: str,
) -> None:
    environment = _enabled_environment(tmp_path)
    environment.update(update)

    assert paper_session_gate_reason(environment) == reason


def test_enabled_config_parses_explicit_paths_without_repr_secrets(tmp_path) -> None:
    environment = _enabled_environment(tmp_path)

    config = KisPaperSessionConfig.from_environment(environment)

    assert config.approved_business_date == date(2026, 7, 10)
    assert config.historical_data_mode == "local_historical"
    assert config.database_path.is_absolute()
    rendered = repr(config)
    assert "secret-app-key" not in rendered
    assert "secret-app-secret" not in rendered
    assert "12345678" not in rendered
    assert "secret-access-token" not in rendered


def test_external_historical_cannot_bypass_gate_with_injected_provider_builder(
    tmp_path,
) -> None:
    environment = _enabled_environment(tmp_path)
    environment["DATA_MODE"] = "external_historical"

    with pytest.raises(
        PaperSessionError,
        match="^paper_external_historical_origin_not_hardened$",
    ):
        KisPaperSessionConfig.from_environment(environment)

    safe_config = KisPaperSessionConfig.from_environment(
        _enabled_environment(tmp_path)
    )
    forged_config = replace(
        safe_config,
        historical_data_mode="external_historical",
    )
    provider_calls = 0

    def injected_provider_builder():
        nonlocal provider_calls
        provider_calls += 1
        return _SecurityProvider(), _Historical()

    with pytest.raises(
        PaperSessionError,
        match="^paper_external_historical_origin_not_hardened$",
    ):
        build_runtime(
            forged_config,
            evaluated_at=NOW,
            provider_builder=injected_provider_builder,
            client_builder=lambda _config: _BuildClient(),  # type: ignore[return-value]
        )

    assert provider_calls == 0


def test_policy_file_must_arrive_already_promoted_for_paper(tmp_path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(_policy().model_dump(mode="json")),
        encoding="utf-8",
    )

    loaded = load_explicit_paper_policy(policy_path)
    assert loaded.policy_id == "paper-policy"
    assert loaded.authority_level == 5
    assert loaded.broker == BrokerMode.paper

    unsafe = _policy().model_copy(
        update={"authority_level": 4, "fully_automated_operator_enabled": False}
    )
    policy_path.write_text(
        json.dumps(unsafe.model_dump(mode="json")),
        encoding="utf-8",
    )
    with pytest.raises(
        PaperSessionError,
        match="^paper_policy_not_explicitly_promoted$",
    ):
        load_explicit_paper_policy(policy_path)


def test_registry_file_requires_exact_recipe_hash_and_promotion_evidence(
    tmp_path,
) -> None:
    registry_path = tmp_path / "registry.json"
    payload = _registry_payload()
    registry_path.write_text(json.dumps(payload), encoding="utf-8")

    registry = load_explicit_paper_registry(registry_path, policy_version=1)

    assert registry.select_for_level5(policy_version=1).selected_strategy_id == (
        "pullback_trend_v2"
    )
    payload["lifecycle_records"][0]["evidence"] = []  # type: ignore[index]
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PaperSessionError, match="strategy_evidence_incomplete"):
        load_explicit_paper_registry(registry_path, policy_version=1)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("confirmation", "generated approval"),
        ("confirmed_by", "   "),
    ],
)
def test_registry_rejects_forged_or_unattributed_promotion_confirmation(
    tmp_path,
    field: str,
    forged_value: str,
) -> None:
    registry_path = tmp_path / "registry.json"
    payload = _registry_payload()
    payload["lifecycle_records"][0]["history"][-1][field] = forged_value  # type: ignore[index]
    registry_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PaperSessionError, match="strategy_evidence_incomplete"):
        load_explicit_paper_registry(registry_path, policy_version=1)


@pytest.mark.parametrize("mutation", ["missing", "foreign"])
def test_registry_rejects_missing_or_foreign_transition_evidence_id(
    tmp_path,
    mutation: str,
) -> None:
    registry_path = tmp_path / "registry.json"
    payload = _registry_payload()
    transition = payload["lifecycle_records"][0]["history"][-1]  # type: ignore[index]
    if mutation == "missing":
        transition["evidence_ids"] = transition["evidence_ids"][:-1]
    else:
        transition["evidence_ids"] = [
            *transition["evidence_ids"][:-1],
            "evid_foreign",
        ]
    registry_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PaperSessionError, match="strategy_evidence_incomplete"):
        load_explicit_paper_registry(registry_path, policy_version=1)


@pytest.mark.parametrize("mutation", ["truncated", "out_of_order", "status_mismatch"])
def test_registry_rejects_incomplete_or_out_of_order_promotion_ladder(
    tmp_path,
    mutation: str,
) -> None:
    registry_path = tmp_path / "registry.json"
    payload = _registry_payload()
    lifecycle = payload["lifecycle_records"][0]  # type: ignore[index]
    if mutation == "truncated":
        lifecycle["history"] = lifecycle["history"][1:]
    elif mutation == "out_of_order":
        lifecycle["history"][1], lifecycle["history"][2] = (
            lifecycle["history"][2],
            lifecycle["history"][1],
        )
    else:
        lifecycle["history"][-1]["to_status"] = "paper_validated"
    registry_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PaperSessionError, match="strategy_evidence_incomplete"):
        load_explicit_paper_registry(registry_path, policy_version=1)


def test_request_identity_is_stable_for_one_utc_minute() -> None:
    first = _operator_request(_policy(), NOW.replace(second=1))
    second = _operator_request(_policy(), NOW.replace(second=59))

    assert first == second
    assert first.run_mode == "paper_submit"
    assert first.requested_at.second == 0


def test_completed_history_excludes_current_session_and_rejects_conflicts() -> None:
    rows = [
        {
            "symbol": "005930",
            "date": "2026-07-09",
            "open": 100,
            "high": 110,
            "low": 90,
            "close": 105,
            "volume": 1000,
        },
        {
            "symbol": "005930",
            "date": "2026-07-10",
            "open": 106,
            "high": 112,
            "low": 100,
            "close": 110,
            "volume": 2000,
        },
    ]

    bars = _completed_pullback_bars(
        rows,
        symbol="005930",
        before_date=date(2026, 7, 10),
    )

    assert [bar.session_date for bar in bars] == [date(2026, 7, 9)]
    conflicting = [dict(rows[0]), dict(rows[0], close=104)]
    with pytest.raises(PaperSessionError, match="history_conflict"):
        _completed_pullback_bars(
            conflicting,
            symbol="005930",
            before_date=date(2026, 7, 10),
        )


class _Coordinator:
    def expire_stale_prepared_dispatches(self):
        return ()


class _Reconciler:
    def reconcile_unresolved(self) -> PaperReconciliationResult:
        return PaperReconciliationResult(
            updated_dispatches=(),
            pending_order_plan_ids=(),
            blocked_order_plan_ids=(),
            broker_balance=_balance(),
            reconciled_at=NOW,
        )


class _Applier:
    def apply(self, _reconciliation) -> PaperReconciliationApplyResult:
        return PaperReconciliationApplyResult(
            applied_order_plan_ids=(),
            missing_order_plan_ids=(),
            blocked_order_plan_ids=(),
            pending_order_plan_ids=(),
            new_fill_ids=(),
            blocked_reasons=(),
        )


class _Store:
    def list_positions(self):
        return []

    def list_paper_order_dispatches(self):
        return []


class _Broker:
    def get_positions(self, user_id: str) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            user_id=user_id,
            cash=1_000_000,
            equity=1_000_000,
            positions=[],
            captured_at=NOW,
            source="fake_reconciled_paper",
        )


class _Historical:
    def get_price_history(self):
        return []

    def get_bars(self):
        return []


class _SecurityProvider:
    def get_securities(self):
        return [{"ticker": "005930", "sector": "technology"}]


class _BuildClient:
    account_scope_fingerprint = "sha256:" + "a" * 64


class _Operator:
    def __init__(self) -> None:
        self.registry = _registry()
        self.repositories = RepositoryRegistry()
        self.professional = object()
        self.calls: list[str] = []

    def run_professional_position_cycle(self, **_kwargs):
        self.calls.append("position")
        return SimpleNamespace(status="no_action", reason_codes=[])

    def run_once(self, request):
        self.calls.append("operator")
        assert request.run_mode == "paper_submit"
        return SimpleNamespace(
            status="completed",
            run_id="run-001",
            fallback=None,
        )


def test_one_shot_runtime_reconciles_then_runs_risk_before_weekly_operator(
    tmp_path,
) -> None:
    operator = _Operator()
    runtime = KisPaperSessionRuntime(
        config=SimpleNamespace(),  # type: ignore[arg-type]
        store=_Store(),  # type: ignore[arg-type]
        session=SimpleNamespace(),  # type: ignore[arg-type]
        coordinator=_Coordinator(),  # type: ignore[arg-type]
        reconciler=_Reconciler(),  # type: ignore[arg-type]
        applier=_Applier(),  # type: ignore[arg-type]
        broker=_Broker(),  # type: ignore[arg-type]
        market_data=SimpleNamespace(),  # type: ignore[arg-type]
        historical_market_data=_Historical(),  # type: ignore[arg-type]
        operator=operator,  # type: ignore[arg-type]
        policy=_policy(),
    )

    result = execute_runtime(runtime, evaluated_at=NOW, clock=lambda: NOW)

    assert result.status == "completed"
    assert result.reason_code == "paper_session_cycle_completed"
    assert result.operator_run_id == "run-001"
    assert operator.calls == ["position", "operator"]


def test_runtime_wiring_binds_one_fenced_store_without_network(tmp_path) -> None:
    environment = _enabled_environment(tmp_path)
    policy_path = tmp_path / "policy.json"
    registry_path = tmp_path / "registry.json"
    policy_path.write_text(
        json.dumps(_policy().model_dump(mode="json")),
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(_registry_payload()),
        encoding="utf-8",
    )
    config = KisPaperSessionConfig.from_environment(environment)

    runtime = build_runtime(
        config,
        evaluated_at=NOW,
        provider_builder=lambda: (_SecurityProvider(), _Historical()),
        client_builder=lambda _config: _BuildClient(),  # type: ignore[return-value]
    )
    try:
        assert runtime.store.provenance.data_mode == "paper_trading"
        assert runtime.store.provenance.account_scope_fingerprint == (
            _BuildClient.account_scope_fingerprint
        )
        assert runtime.session.status == "active"
        assert runtime.operator.harness.external_paper_enabled is True
    finally:
        runtime.store.close_paper_execution_session(
            runtime.session,
            closed_at=NOW.replace(microsecond=1),
        )
        runtime.store.close()
