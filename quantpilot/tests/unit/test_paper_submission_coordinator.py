from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from quantpilot.packages.core.execution.paper_submission import (
    DurablePaperSubmissionCoordinator,
    PaperPreDispatchFailure,
    PaperSubmissionOutcomeUnknown,
    PaperSubmissionRejected,
)
from quantpilot.packages.core.kis_paper import (
    KisBuyingPower,
    KisCashOrderResult,
    KisPaperBusinessError,
    KisPaperOrderOutcomeUnknown,
)
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.schemas import (
    OrderIntent,
    OrderPlan,
    OrderStatus,
    PortfolioPosition,
    PortfolioSnapshot,
    ProposalExplanation,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
FINGERPRINT = "sha256:" + "a" * 64


class MutableClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeSessionAuthority:
    def __init__(
        self,
        *,
        open_now: bool = True,
        close_after_calls: int | None = None,
    ) -> None:
        self.open_now = open_now
        self.close_after_calls = close_after_calls
        self.calls = 0

    def current_open_session_date(self, observed_at: datetime) -> date | None:
        self.calls += 1
        if not self.open_now or (
            self.close_after_calls is not None
            and self.calls > self.close_after_calls
        ):
            return None
        return observed_at.astimezone(timezone(timedelta(hours=9))).date()


class FakePaperClient:
    account_scope_fingerprint = FINGERPRINT

    def __init__(self) -> None:
        self.buying_power_calls = 0
        self.order_calls = 0
        self.order_outcome: KisCashOrderResult | BaseException = KisCashOrderResult(
            symbol="005930",
            side="buy",
            quantity=1,
            limit_price=Decimal("70000"),
            order_number="0000012345",
            krx_forwarding_order_org_number="91234",
            order_time="100001",
            message_code="APBK0013",
            transaction_id="VTTC0012U",
        )
        self.buying_power = KisBuyingPower(
            symbol="005930",
            limit_price=Decimal("70000"),
            orderable_cash=Decimal("500000"),
            no_receivable_buy_amount=Decimal("450000"),
            no_receivable_buy_quantity=6,
            maximum_buy_amount=Decimal("900000"),
            maximum_buy_quantity=12,
            calculation_price=Decimal("70000"),
        )

    def get_buying_power(
        self,
        symbol: str,
        limit_price: Decimal,
        *,
        exchange: str = "KRX",
    ) -> KisBuyingPower:
        assert symbol == "005930"
        assert limit_price == Decimal("70000")
        assert exchange == "KRX"
        self.buying_power_calls += 1
        return self.buying_power

    def place_limit_cash_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        limit_price: Decimal,
        exchange: str = "KRX",
    ) -> KisCashOrderResult:
        assert (symbol, side, quantity, limit_price, exchange) == (
            "005930",
            "buy",
            1,
            Decimal("70000"),
            "KRX",
        )
        self.order_calls += 1
        if isinstance(self.order_outcome, BaseException):
            raise self.order_outcome
        return self.order_outcome


def _explanation() -> ProposalExplanation:
    return ProposalExplanation(
        symbol="005930",
        action="buy",
        quantity=1,
        target_weight_delta=0.07,
        reference_price=70000,
        estimated_cash_impact=70000,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        signal_reason="fixture",
        current_weight=0,
        target_weight=0.07,
        weight_delta=0.07,
        quote_price=70000,
        quote_age_seconds=5,
        limit_price=70000,
        estimated_notional=70000,
        risk_checks_passed=["all"],
        risk_check_id="risk-001",
        risk_check_expires_at=NOW + timedelta(minutes=5),
        idempotency_key="paper-order-key-001",
        policy_version=1,
    )


def _order(**updates: object) -> OrderPlan:
    values: dict[str, object] = {
        "order_plan_id": "plan-001",
        "policy_id": "policy-001",
        "policy_version": 1,
        "intent": OrderIntent(
            symbol="005930",
            side="buy",
            quantity=1,
            limit_price=70000,
            notional=70000,
            target_weight=0.07,
            reason="fixture",
            quote_time=NOW - timedelta(seconds=5),
        ),
        "status": OrderStatus.submitted,
        "idempotency_key": "paper-order-key-001",
        "risk_check_id": "risk-001",
        "risk_check_expires_at": NOW + timedelta(minutes=5),
        "explanation": _explanation(),
    }
    values.update(updates)
    return OrderPlan(**values)


def _snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="snapshot-001",
        user_id="paper-user",
        cash=300000,
        equity=1000000,
        positions=[],
        daily_loss_ratio=-0.01,
        monthly_loss_ratio=-0.02,
        captured_at=NOW - timedelta(seconds=4),
        source="kis_paper_balance_reconciled",
    )


def _quote() -> Quote:
    return Quote(
        symbol="005930",
        last=70000,
        bid=69900,
        ask=70100,
        as_of=NOW - timedelta(seconds=5),
    )


def _coordinator(
    store: PaperStateStore,
    client: FakePaperClient,
    clock: MutableClock,
    *,
    started_at: datetime = NOW - timedelta(minutes=1),
    lease_expires_at: datetime = NOW + timedelta(hours=1),
    session_authority: FakeSessionAuthority | None = None,
):
    session = store.start_paper_execution_session(
        started_at=started_at,
        lease_expires_at=lease_expires_at,
    )
    return DurablePaperSubmissionCoordinator(
        store=store,
        session=session,
        client=client,  # type: ignore[arg-type]
        session_authority=session_authority or FakeSessionAuthority(),
        clock=clock,
    )


def test_durable_kill_fences_prepare_and_pre_post_submission(tmp_path) -> None:
    client = FakePaperClient()
    clock = MutableClock()
    with PaperStateStore(
        tmp_path / "kill-fence.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, client, clock)
        order = _order()
        coordinator.prepare_order(
            order,
            run_id="run-kill-fence",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )
        store.start_paper_kill_operation(
            session=coordinator.session,
            reason="operator_requested",
            started_at=NOW + timedelta(microseconds=1),
        )

        with pytest.raises(PaperPreDispatchFailure, match="paper kill"):
            coordinator.submit_prepared_order(order)
        assert client.order_calls == 0
        assert store.load_paper_order_dispatch(order.order_plan_id).status == (
            "expired_pre_dispatch"
        )
        released = store.load_paper_risk_reservation(order.order_plan_id)
        assert released is not None
        assert released.status == "released_expired"

        with pytest.raises(RuntimeError, match="paper_kill_blocks_submission"):
            coordinator.prepare_order(
                _order(order_plan_id="plan-002", idempotency_key="paper-order-key-002"),
                run_id="run-kill-fence",
                user_id="paper-user",
                snapshot=_snapshot(),
                quote=_quote(),
                entry_atr14=1200,
                quote_max_age_seconds=30,
                snapshot_max_age_seconds=30,
                minimum_cash_reserve=200000,
            )


def test_kill_terminalizes_prepared_dispatch_without_broker_post(tmp_path) -> None:
    client = FakePaperClient()
    clock = MutableClock()
    with PaperStateStore(
        tmp_path / "kill-terminalize.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, client, clock)
        coordinator.prepare_order(
            _order(),
            run_id="run-kill-terminalize",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )
        store.start_paper_kill_operation(
            session=coordinator.session,
            reason="operator_requested",
            started_at=NOW + timedelta(microseconds=1),
        )
        terminal = coordinator.terminalize_prepared_dispatches_for_kill()

        assert [item.status for item in terminal] == ["expired_pre_dispatch"]
        assert terminal[0].last_error_code == "paper_kill_engaged"
        released = store.load_paper_risk_reservation(terminal[0].order_plan_id)
        assert released is not None
        assert released.status == "released_expired"
        assert client.order_calls == 0


def test_prepare_commits_exact_evidence_before_single_post_and_replays_without_post(
    tmp_path,
) -> None:
    client = FakePaperClient()
    clock = MutableClock()
    with PaperStateStore(
        tmp_path / "paper.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, client, clock)
        order = _order()

        prepared = coordinator.prepare_order(
            order,
            run_id="run-001",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )

        assert prepared.status == "prepared"
        assert prepared.attempt_count == 0
        assert prepared.broker_orderable_cash == 250000
        assert prepared.broker_orderable_buy_quantity == 6
        assert prepared.quote_reference_basis == "l2_midpoint"
        reservation = store.load_paper_risk_reservation(order.order_plan_id)
        assert reservation is not None
        assert reservation.status == "held"
        assert reservation.reserved_cash_krw == 70_000
        assert reservation.reserved_gross_exposure_krw == 70_000
        assert reservation.broker_orderable_cash_basis_krw == 250_000
        assert reservation.broker_orderable_buy_quantity_basis == 6
        assert reservation.snapshot_gross_exposure_basis_krw == 700_000
        assert reservation.minimum_cash_reserve_krw == 200_000
        assert reservation.gross_exposure_limit_krw == 800_000
        for integer_value in (
            reservation.reserved_cash_krw,
            reservation.reserved_gross_exposure_krw,
            reservation.broker_orderable_cash_basis_krw,
            reservation.broker_orderable_buy_quantity_basis,
            reservation.snapshot_gross_exposure_basis_krw,
            reservation.minimum_cash_reserve_krw,
            reservation.gross_exposure_limit_krw,
        ):
            assert type(integer_value) is int
        assert client.buying_power_calls == 1
        assert client.order_calls == 0

        with pytest.raises(ValueError, match="cash-reserve evidence changed"):
            coordinator.prepare_order(
                order,
                run_id="run-001",
                user_id="paper-user",
                snapshot=_snapshot(),
                quote=_quote(),
                entry_atr14=1200,
                quote_max_age_seconds=30,
                snapshot_max_age_seconds=30,
                minimum_cash_reserve=200001,
            )
        assert client.buying_power_calls == 1
        assert client.order_calls == 0

        broker_order, fills = coordinator.submit_prepared_order(order)
        persisted = store.load_paper_order_dispatch(order.order_plan_id)
        assert persisted is not None
        assert persisted.status == "accepted"
        assert persisted.attempt_count == 1
        assert persisted.broker_order_reference == "0000012345"
        assert persisted.broker_forwarding_order_org_number == "91234"
        assert persisted.broker_order_branch_number is None
        assert broker_order.broker_order_id == prepared.broker_order_id
        assert fills == []
        assert client.order_calls == 1

        replayed, replayed_fills = coordinator.submit_prepared_order(order)
        assert replayed == broker_order
        assert replayed_fills == []
        assert client.order_calls == 1


def test_fractional_capacity_inputs_round_conservatively(tmp_path) -> None:
    client = FakePaperClient()
    client.buying_power = replace(
        client.buying_power,
        orderable_cash=Decimal("500000.9"),
        no_receivable_buy_amount=Decimal("450000.9"),
    )
    snapshot = PortfolioSnapshot(
        snapshot_id="snapshot-fractional-capacity",
        user_id="paper-user",
        cash=300_000.6,
        equity=1_000_000.9,
        positions=[],
        daily_loss_ratio=-0.01,
        monthly_loss_ratio=-0.02,
        captured_at=NOW - timedelta(seconds=4),
        source="kis_paper_balance_reconciled",
    )
    with PaperStateStore(
        tmp_path / "fractional-capacity.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, client, MutableClock())
        prepared = coordinator.prepare_order(
            _order(),
            run_id="run-fractional-capacity",
            user_id="paper-user",
            snapshot=snapshot,
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200_000.2,
        )
        reservation = store.load_paper_risk_reservation(
            prepared.order_plan_id
        )

        assert reservation is not None
        assert prepared.broker_orderable_cash == 249_999
        assert reservation.broker_orderable_cash_basis_krw == 249_999
        assert reservation.minimum_cash_reserve_krw == 200_001
        assert reservation.snapshot_gross_exposure_basis_krw == 700_001
        assert reservation.gross_exposure_limit_krw == 799_999
        assert client.order_calls == 0


def test_migrated_open_dispatch_reprepare_uses_backfilled_cash_reserve(
    tmp_path,
) -> None:
    path = tmp_path / "migrated-reprepare.sqlite3"
    client = FakePaperClient()
    order = _order()
    with PaperStateStore(
        path,
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, client, MutableClock())
        prepared = coordinator.prepare_order(
            order,
            run_id="run-migrated-reprepare",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )
        session_id = coordinator.session.session_id

    connection = sqlite3.connect(path)
    try:
        metadata = json.loads(
            connection.execute(
                "SELECT state_json FROM state_store_metadata"
            ).fetchone()[0]
        )
        metadata["schema_version"] = 9
        dispatch_state = json.loads(
            connection.execute(
                "SELECT state_json FROM paper_order_dispatches"
            ).fetchone()[0]
        )
        dispatch_state.pop("minimum_cash_reserve_krw", None)
        connection.execute("DROP TABLE paper_risk_reservations")
        connection.execute(
            "UPDATE paper_order_dispatches SET state_json = ?",
            (json.dumps(dispatch_state, separators=(",", ":"), sort_keys=True),),
        )
        connection.execute(
            "UPDATE state_store_metadata SET schema_version = 9, state_json = ?",
            (json.dumps(metadata, separators=(",", ":"), sort_keys=True),),
        )
        connection.execute("PRAGMA user_version = 9")
        connection.commit()
    finally:
        connection.close()

    with PaperStateStore(
        path,
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as reopened:
        session = reopened.load_paper_execution_session(session_id)
        assert session is not None
        coordinator = DurablePaperSubmissionCoordinator(
            store=reopened,
            session=session,
            client=client,  # type: ignore[arg-type]
            session_authority=FakeSessionAuthority(),
            clock=MutableClock(),
        )
        migrated = reopened.load_paper_order_dispatch(prepared.order_plan_id)
        reservation = reopened.load_paper_risk_reservation(
            prepared.order_plan_id
        )
        assert migrated is not None
        assert migrated.minimum_cash_reserve_krw is None
        assert reservation is not None
        assert reservation.minimum_cash_reserve_krw == 230_000

        with pytest.raises(ValueError, match="cash-reserve evidence changed"):
            coordinator.prepare_order(
                order,
                run_id="run-migrated-reprepare",
                user_id="paper-user",
                snapshot=_snapshot(),
                quote=_quote(),
                entry_atr14=1200,
                quote_max_age_seconds=30,
                snapshot_max_age_seconds=30,
                minimum_cash_reserve=200000,
            )
        replay = coordinator.prepare_order(
            order,
            run_id="run-migrated-reprepare",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=230000,
        )
        assert replay == migrated
        assert client.buying_power_calls == 1
        assert client.order_calls == 0


@pytest.mark.parametrize(
    ("outcome", "exception_type", "expected_status"),
    [
        (
            KisPaperOrderOutcomeUnknown("fake reset with fake-secret"),
            PaperSubmissionOutcomeUnknown,
            "outcome_unknown",
        ),
        (
            KisPaperBusinessError("fake definitive reject"),
            PaperSubmissionRejected,
            "rejected",
        ),
    ],
)
def test_unknown_is_never_retried_and_business_reject_is_terminal(
    tmp_path,
    outcome: BaseException,
    exception_type: type[Exception],
    expected_status: str,
) -> None:
    client = FakePaperClient()
    client.order_outcome = outcome
    clock = MutableClock()
    with PaperStateStore(
        tmp_path / f"{expected_status}.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, client, clock)
        order = _order()
        coordinator.prepare_order(
            order,
            run_id="run-001",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )

        with pytest.raises(exception_type):
            coordinator.submit_prepared_order(order)
        persisted = store.load_paper_order_dispatch(order.order_plan_id)
        assert persisted is not None and persisted.status == expected_status
        assert persisted.attempt_count == 1
        reservation = store.load_paper_risk_reservation(order.order_plan_id)
        assert reservation is not None
        assert reservation.status == (
            "held" if expected_status == "outcome_unknown" else "released_rejected"
        )
        with pytest.raises(exception_type):
            coordinator.submit_prepared_order(order)
        assert client.order_calls == 1


def test_process_interrupt_after_claim_recovers_query_only_and_never_reposts(
    tmp_path,
) -> None:
    client = FakePaperClient()
    client.order_outcome = KeyboardInterrupt()
    clock = MutableClock()
    path = tmp_path / "crash.sqlite3"
    with PaperStateStore(
        path,
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(
            store,
            client,
            clock,
            lease_expires_at=NOW + timedelta(minutes=1),
        )
        order = _order()
        coordinator.prepare_order(
            order,
            run_id="run-001",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )
        with pytest.raises(KeyboardInterrupt):
            coordinator.submit_prepared_order(order)
        claimed = store.load_paper_order_dispatch(order.order_plan_id)
        assert claimed is not None and claimed.status == "dispatch_claimed"
        assert client.order_calls == 1

        recovered_at = NOW + timedelta(minutes=2)
        clock.now = recovered_at
        successor = store.start_paper_execution_session(
            started_at=recovered_at,
            lease_expires_at=recovered_at + timedelta(hours=1),
        )
        recovered = store.recover_interrupted_dispatches(
            session=successor,
            recovered_at=recovered_at,
        )
        assert [item.status for item in recovered] == ["outcome_unknown"]
        restarted = DurablePaperSubmissionCoordinator(
            store=store,
            session=successor,
            client=client,  # type: ignore[arg-type]
            session_authority=FakeSessionAuthority(),
            clock=clock,
        )
        with pytest.raises(PaperSubmissionOutcomeUnknown):
            restarted.submit_prepared_order(order)
        assert client.order_calls == 1


def test_expired_risk_and_insufficient_buying_power_stop_before_post(tmp_path) -> None:
    client = FakePaperClient()
    clock = MutableClock()
    with PaperStateStore(
        tmp_path / "gates.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, client, clock)
        order = _order()
        coordinator.prepare_order(
            order,
            run_id="run-001",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )
        clock.now = NOW + timedelta(minutes=6)
        with pytest.raises(PaperPreDispatchFailure):
            coordinator.submit_prepared_order(order)
        terminal = store.load_paper_order_dispatch(order.order_plan_id)
        assert terminal is not None and terminal.status == "expired_pre_dispatch"
        assert terminal.attempt_count == 0
        assert client.order_calls == 0

    poor = FakePaperClient()
    poor.buying_power = poor.buying_power.__class__(
        **{
            **poor.buying_power.__dict__,
            "no_receivable_buy_amount": Decimal("100"),
        }
    )
    with PaperStateStore(
        tmp_path / "poor.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, poor, MutableClock())
        with pytest.raises(ValueError, match="broker cash"):
            coordinator.prepare_order(
                _order(),
                run_id="run-001",
                user_id="paper-user",
                snapshot=_snapshot(),
                quote=_quote(),
                entry_atr14=1200,
                quote_max_age_seconds=30,
                snapshot_max_age_seconds=30,
                minimum_cash_reserve=200000,
            )
        assert store.list_paper_order_dispatches() == []
        assert poor.order_calls == 0


def test_wrong_account_provenance_fails_before_any_client_query(tmp_path) -> None:
    client = FakePaperClient()
    clock = MutableClock()
    with PaperStateStore(
        tmp_path / "wrong.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint="sha256:" + "b" * 64,
    ) as store:
        session = store.start_paper_execution_session(
            started_at=NOW - timedelta(minutes=1),
            lease_expires_at=NOW + timedelta(hours=1),
        )
        with pytest.raises(ValueError, match="provenance must match"):
            DurablePaperSubmissionCoordinator(
                store=store,
                session=session,
                client=client,  # type: ignore[arg-type]
                session_authority=FakeSessionAuthority(),
                clock=clock,
            )
    assert client.buying_power_calls == 0
    assert client.order_calls == 0


def test_sell_uses_snapshot_orderable_quantity_without_buying_power_query(tmp_path) -> None:
    client = FakePaperClient()
    clock = MutableClock()
    sell_explanation = _explanation().model_copy(
        update={"action": "sell", "estimated_cash_impact": -70000}
    )
    sell_order = _order(
        intent=OrderIntent(
            symbol="005930",
            side="sell",
            quantity=1,
            limit_price=70000,
            notional=70000,
            target_weight=0.63,
            reason="risk exit",
            quote_time=NOW - timedelta(seconds=5),
        ),
        purpose="protective_exit",
        explanation=sell_explanation,
    )
    snapshot = _snapshot().model_copy(
        update={
            "positions": [
                PortfolioPosition(
                    symbol="005930",
                    quantity=10,
                    orderable_quantity=1,
                    market_price=70000,
                    sector="technology",
                )
            ],
            "cash": 300000,
            "equity": 1000000,
        }
    )
    with PaperStateStore(
        tmp_path / "sell.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, client, clock)
        prepared = coordinator.prepare_order(
            sell_order,
            run_id="run-001",
            user_id="paper-user",
            snapshot=snapshot,
            quote=_quote(),
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )
        assert prepared.snapshot_symbol_quantity == 10
        assert prepared.snapshot_symbol_orderable_quantity == 1
        assert prepared.broker_orderable_cash is None
        assert client.buying_power_calls == 0

        oversized_intent = sell_order.intent.model_copy(
            update={"quantity": 2, "notional": 140000}
        )
        oversized_explanation = sell_explanation.model_copy(
            update={"quantity": 2, "estimated_cash_impact": -140000}
        )
        oversized_order = sell_order.model_copy(
            update={
                "order_plan_id": "plan-oversized-sell",
                "idempotency_key": "paper-order-key-oversized-sell",
                "intent": oversized_intent,
                "explanation": oversized_explanation,
            }
        )
        with pytest.raises(
            ValueError,
            match="paper sell exceeds snapshot orderable quantity",
        ):
            coordinator.prepare_order(
                oversized_order,
                run_id="run-oversized",
                user_id="paper-user",
                snapshot=snapshot,
                quote=_quote(),
                quote_max_age_seconds=30,
                snapshot_max_age_seconds=30,
                minimum_cash_reserve=200000,
            )
        assert client.buying_power_calls == 0
        assert client.order_calls == 0


def test_fractional_sell_orderable_quantity_fails_closed(tmp_path) -> None:
    client = FakePaperClient()
    sell_explanation = _explanation().model_copy(
        update={"action": "sell", "estimated_cash_impact": -70000}
    )
    sell_order = _order(
        intent=OrderIntent(
            symbol="005930",
            side="sell",
            quantity=1,
            limit_price=70000,
            notional=70000,
            target_weight=0.63,
            reason="risk exit",
            quote_time=NOW - timedelta(seconds=5),
        ),
        purpose="protective_exit",
        explanation=sell_explanation,
    )
    snapshot = _snapshot().model_copy(
        update={
            "positions": [
                PortfolioPosition(
                    symbol="005930",
                    quantity=10,
                    orderable_quantity=1.5,
                    market_price=70000,
                    sector="technology",
                )
            ]
        }
    )
    with PaperStateStore(
        tmp_path / "fractional-sell.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, client, MutableClock())
        with pytest.raises(ValueError, match="nonnegative whole number"):
            coordinator.prepare_order(
                sell_order,
                run_id="run-fractional-sell",
                user_id="paper-user",
                snapshot=snapshot,
                quote=_quote(),
                quote_max_age_seconds=30,
                snapshot_max_age_seconds=30,
                minimum_cash_reserve=200000,
            )
        assert store.list_paper_order_dispatches() == []
        assert store.list_paper_risk_reservations() == []
        assert client.buying_power_calls == 0
        assert client.order_calls == 0


def test_session_closure_is_rechecked_immediately_before_claim_and_post(
    tmp_path,
) -> None:
    client = FakePaperClient()
    clock = MutableClock()
    authority = FakeSessionAuthority()
    with PaperStateStore(
        tmp_path / "session-boundary.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(
            store,
            client,
            clock,
            session_authority=authority,
        )
        order = _order()
        coordinator.prepare_order(
            order,
            run_id="run-session-boundary",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )

        authority.open_now = False
        with pytest.raises(PaperPreDispatchFailure, match="session closed"):
            coordinator.submit_prepared_order(order)

        terminal = store.load_paper_order_dispatch(order.order_plan_id)
        assert terminal is not None
        assert terminal.status == "failed_pre_dispatch"
        assert terminal.attempt_count == 0
        assert terminal.last_error_code == "paper_session_closed_before_dispatch"
        assert client.order_calls == 0


def test_session_closure_after_claim_is_terminalized_without_broker_post(
    tmp_path,
) -> None:
    client = FakePaperClient()
    clock = MutableClock()
    authority = FakeSessionAuthority(close_after_calls=1)
    with PaperStateStore(
        tmp_path / "post-claim-session-boundary.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(
            store,
            client,
            clock,
            session_authority=authority,
        )
        order = _order()
        coordinator.prepare_order(
            order,
            run_id="run-post-claim-session-boundary",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )

        with pytest.raises(PaperSubmissionRejected, match="after claim"):
            coordinator.submit_prepared_order(order)

        terminal = store.load_paper_order_dispatch(order.order_plan_id)
        assert terminal is not None
        assert terminal.status == "rejected"
        assert terminal.attempt_count == 1
        assert terminal.last_error_code == "paper_session_closed_after_claim"
        assert terminal.reconciliation_status == "reconciled"
        assert client.order_calls == 0


@pytest.mark.parametrize("stale_evidence", ["quote", "snapshot"])
def test_stale_submission_evidence_blocks_before_buying_power_or_post(
    tmp_path,
    stale_evidence: str,
) -> None:
    client = FakePaperClient()
    clock = MutableClock()
    quote = _quote()
    snapshot = _snapshot()
    if stale_evidence == "quote":
        quote = quote.model_copy(
            update={"as_of": NOW - timedelta(seconds=31)}
        )
    else:
        snapshot = snapshot.model_copy(
            update={"captured_at": NOW - timedelta(seconds=31)}
        )
    with PaperStateStore(
        tmp_path / f"stale-{stale_evidence}.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        coordinator = _coordinator(store, client, clock)

        with pytest.raises(ValueError, match=f"{stale_evidence} is stale"):
            coordinator.prepare_order(
                _order(),
                run_id="run-001",
                user_id="paper-user",
                snapshot=snapshot,
                quote=quote,
                quote_max_age_seconds=30,
                snapshot_max_age_seconds=30,
                minimum_cash_reserve=200000,
            )

        assert client.buying_power_calls == 0
        assert client.order_calls == 0
        assert store.list_paper_order_dispatches() == []


def test_restart_expires_prepared_record_without_any_post(tmp_path) -> None:
    client = FakePaperClient()
    first_clock = MutableClock()
    with PaperStateStore(
        tmp_path / "prepared-restart.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        first = _coordinator(
            store,
            client,
            first_clock,
            lease_expires_at=NOW + timedelta(seconds=40),
        )
        prepared = first.prepare_order(
            _order(),
            run_id="run-001",
            user_id="paper-user",
            snapshot=_snapshot(),
            quote=_quote(),
            entry_atr14=1200,
            quote_max_age_seconds=30,
            snapshot_max_age_seconds=30,
            minimum_cash_reserve=200000,
        )
        assert prepared.status == "prepared"
        assert client.order_calls == 0

        recovery_time = NOW + timedelta(seconds=41)
        recovery_session = store.start_paper_execution_session(
            started_at=recovery_time,
            lease_expires_at=recovery_time + timedelta(minutes=5),
        )
        recovered = DurablePaperSubmissionCoordinator(
            store=store,
            session=recovery_session,
            client=client,  # type: ignore[arg-type]
            session_authority=FakeSessionAuthority(),
            clock=lambda: recovery_time,
        ).expire_stale_prepared_dispatches()

        assert len(recovered) == 1
        assert recovered[0].status == "expired_pre_dispatch"
        assert recovered[0].attempt_count == 0
        assert recovered[0].session_id == recovery_session.session_id
        released = store.load_paper_risk_reservation(prepared.order_plan_id)
        assert released is not None
        assert released.status == "released_expired"
        assert released.session_id == recovery_session.session_id
        assert released.fencing_token == recovery_session.fencing_token
        assert client.order_calls == 0


def test_no_other_production_module_calls_the_low_level_order_post() -> None:
    package_root = Path(__file__).parents[2] / "packages"
    callers = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*.py")
        if "place_limit_cash_order(" in path.read_text(encoding="utf-8")
    }

    assert callers == {
        "core/kis_paper.py",
        "core/execution/paper_submission.py",
    }
