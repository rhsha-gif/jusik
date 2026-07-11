from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import isclose, isfinite
from typing import Protocol
from zoneinfo import ZoneInfo

from quantpilot.packages.core.kis_paper import (
    KisBuyingPower,
    KisCashOrderResult,
    KisPaperBusinessError,
    KisPaperClient,
    KisPaperConfigurationError,
    KisPaperOrderOutcomeUnknown,
)
from quantpilot.packages.core.marketdata.kis_paper import (
    PaperTradingSessionAuthority,
)
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.position_ledger import (
    PaperExecutionSession,
    PaperOrderDispatch,
    PaperRiskReservation,
)
from quantpilot.packages.core.schemas import (
    BrokerMode,
    BrokerOrder,
    Fill,
    OrderPlan,
    OrderStatus,
    PortfolioSnapshot,
    utc_now,
)


KST = ZoneInfo("Asia/Seoul")


class PaperSubmissionError(RuntimeError):
    reason_code = "paper_submission_error"

    def __init__(self, dispatch: PaperOrderDispatch, message: str) -> None:
        super().__init__(message)
        self.dispatch = dispatch


class PaperPreDispatchFailure(PaperSubmissionError):
    reason_code = "paper_pre_dispatch_failure"


class PaperSubmissionRejected(PaperSubmissionError):
    reason_code = "paper_submission_rejected"


class PaperSubmissionOutcomeUnknown(PaperSubmissionError):
    reason_code = "paper_submission_outcome_unknown"


class PaperDispatchStore(Protocol):
    @property
    def provenance(self): ...

    def reserve_and_insert_paper_order_dispatch(
        self,
        dispatch: PaperOrderDispatch,
        reservation: PaperRiskReservation,
    ) -> tuple[PaperOrderDispatch, PaperRiskReservation]: ...

    def load_paper_risk_reservation(
        self,
        order_plan_id: str,
    ) -> PaperRiskReservation | None: ...

    def load_paper_order_dispatch(
        self,
        order_plan_id: str,
    ) -> PaperOrderDispatch | None: ...

    def find_paper_order_dispatch_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> PaperOrderDispatch | None: ...

    def list_paper_order_dispatches(self) -> list[PaperOrderDispatch]: ...

    def paper_kill_blocks_submission(self) -> bool: ...

    def takeover_prepared_paper_order_dispatch(
        self,
        order_plan_id: str,
        *,
        session: PaperExecutionSession,
        taken_over_at: datetime,
    ) -> PaperOrderDispatch: ...

    def claim_dispatch_attempt(
        self,
        order_plan_id: str,
        *,
        session: PaperExecutionSession,
        claimed_at: datetime,
    ) -> PaperOrderDispatch: ...

    def update_paper_order_dispatch(
        self,
        dispatch: PaperOrderDispatch,
    ) -> PaperOrderDispatch: ...


class DurablePaperSubmissionCoordinator:
    """Sole KIS POST authority backed by a process-kill-safe SQLite journal."""

    def __init__(
        self,
        *,
        store: PaperDispatchStore,
        session: PaperExecutionSession,
        client: KisPaperClient,
        session_authority: PaperTradingSessionAuthority,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        provenance = store.provenance
        if (
            provenance.data_mode != "paper_trading"
            or provenance.broker_environment != "kis_paper"
            or provenance.store_id != session.store_id
            or provenance.account_scope_fingerprint
            != client.account_scope_fingerprint
            or session.account_scope_fingerprint
            != client.account_scope_fingerprint
            or session.data_mode != "paper_trading"
            or session.broker_environment != "kis_paper"
            or session.status != "active"
        ):
            raise ValueError(
                "paper coordinator store, session, and client provenance must match"
            )
        self._store = store
        self._session = session
        self._client = client
        self._session_authority = session_authority
        self._clock = clock

    @property
    def session(self) -> PaperExecutionSession:
        return self._session.model_copy(deep=True)

    @property
    def store(self) -> PaperDispatchStore:
        """Return the provenance-bound journal used by this coordinator."""

        return self._store

    def bind_renewed_session(self, session: PaperExecutionSession) -> None:
        if (
            session.session_id != self._session.session_id
            or session.store_id != self._session.store_id
            or session.fencing_token != self._session.fencing_token
            or session.account_scope_fingerprint
            != self._session.account_scope_fingerprint
            or session.status != "active"
            or session.revision < self._session.revision
        ):
            raise ValueError("paper coordinator cannot bind a foreign session fence")
        self._session = session

    def expire_stale_prepared_dispatches(
        self,
    ) -> tuple[PaperOrderDispatch, ...]:
        """Terminalize expired pre-POST records without crossing the broker boundary."""

        now = self._now()
        expired: list[PaperOrderDispatch] = []
        for dispatch in self._store.list_paper_order_dispatches():
            if (
                dispatch.status != "prepared"
                or dispatch.submission_evidence_expires_at > now
            ):
                continue
            current = dispatch
            if (
                current.session_id != self._session.session_id
                or current.fencing_token != self._session.fencing_token
            ):
                current = self._store.takeover_prepared_paper_order_dispatch(
                    current.order_plan_id,
                    session=self._session,
                    taken_over_at=max(
                        now,
                        current.updated_at + timedelta(microseconds=1),
                    ),
                )
            expired.append(
                self._terminal_pre_dispatch(
                    current,
                    status="expired_pre_dispatch",
                    error_code="submission_evidence_expired",
                    at=max(
                        now,
                        current.updated_at + timedelta(microseconds=1),
                    ),
                )
            )
        return tuple(expired)

    def terminalize_prepared_dispatches_for_kill(
        self,
    ) -> tuple[PaperOrderDispatch, ...]:
        """Discard every unattempted order after a durable paper kill fence."""

        now = self._now()
        terminal: list[PaperOrderDispatch] = []
        for dispatch in self._store.list_paper_order_dispatches():
            if dispatch.status != "prepared":
                continue
            current = dispatch
            write_at = self._strictly_later(now, current.updated_at)
            if (
                current.session_id != self._session.session_id
                or current.fencing_token != self._session.fencing_token
            ):
                current = self._store.takeover_prepared_paper_order_dispatch(
                    current.order_plan_id,
                    session=self._session,
                    taken_over_at=write_at,
                )
                write_at = self._strictly_later(self._now(), current.updated_at)
            terminal.append(
                self._terminal_pre_dispatch(
                    current,
                    status="expired_pre_dispatch",
                    error_code="paper_kill_engaged",
                    at=write_at,
                )
            )
        return tuple(terminal)

    def prepare_order(
        self,
        order_plan: OrderPlan,
        *,
        run_id: str,
        user_id: str,
        snapshot: PortfolioSnapshot,
        quote: Quote,
        entry_atr14: float | None = None,
        quote_max_age_seconds: int,
        snapshot_max_age_seconds: int,
        minimum_cash_reserve: float,
    ) -> PaperOrderDispatch:
        if self._store.paper_kill_blocks_submission():
            raise RuntimeError("paper_kill_blocks_submission")
        if (
            not isfinite(minimum_cash_reserve)
            or minimum_cash_reserve < 0
            or minimum_cash_reserve > snapshot.equity
        ):
            raise ValueError("paper dispatch cash reserve is outside the portfolio")
        minimum_cash_reserve_krw = int(
            Decimal(str(minimum_cash_reserve)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        existing = self._store.find_paper_order_dispatch_by_idempotency_key(
            order_plan.idempotency_key
        )
        if existing is not None:
            self._require_order_matches_dispatch(order_plan, existing)
            existing_reservation = None
            if existing.status in {
                "prepared",
                "dispatch_claimed",
                "outcome_unknown",
                "accepted",
                "partially_filled",
            }:
                existing_reservation = self._store.load_paper_risk_reservation(
                    existing.order_plan_id
                )
                if existing_reservation is None:
                    raise RuntimeError(
                        "open paper dispatch is missing its risk reservation"
                    )
            durable_cash_reserve = existing.minimum_cash_reserve_krw
            if durable_cash_reserve is None and existing_reservation is not None:
                durable_cash_reserve = (
                    existing_reservation.minimum_cash_reserve_krw
                )
            if (
                durable_cash_reserve is not None
                and durable_cash_reserve != minimum_cash_reserve_krw
            ):
                raise ValueError(
                    "paper order cash-reserve evidence changed for its idempotency key"
                )
            return existing

        prepared_at = self._now()
        self._require_preparable_order(
            order_plan,
            snapshot=snapshot,
            quote=quote,
            prepared_at=prepared_at,
            quote_max_age_seconds=quote_max_age_seconds,
            snapshot_max_age_seconds=snapshot_max_age_seconds,
        )
        if snapshot.user_id != user_id:
            raise ValueError("paper dispatch user does not match the broker snapshot")
        explanation = order_plan.explanation
        if explanation is None:
            raise ValueError("external paper orders require strategy explanation evidence")
        if (
            explanation.symbol.strip().upper() != order_plan.intent.symbol.strip().upper()
            or explanation.action != order_plan.intent.side
            or explanation.policy_version != order_plan.policy_version
        ):
            raise ValueError("paper order explanation does not match the order identity")

        quantity = int(_whole_positive_number(order_plan.intent.quantity, "quantity"))
        limit_price = int(_whole_positive_number(
            order_plan.intent.limit_price,
            "limit price",
        ))
        symbol = order_plan.intent.symbol.strip().upper()
        held_quantity_raw, orderable_quantity_raw = _snapshot_symbol_quantities(
            snapshot,
            symbol,
        )
        held_quantity = _whole_nonnegative_number(
            held_quantity_raw,
            "snapshot held quantity",
        )
        orderable_quantity = _whole_nonnegative_number(
            orderable_quantity_raw,
            "snapshot orderable quantity",
        )
        if (
            order_plan.intent.side == "sell"
            and quantity > orderable_quantity
        ):
            raise ValueError("paper sell exceeds snapshot orderable quantity")
        buying_power: KisBuyingPower | None = None
        if order_plan.intent.side == "buy":
            try:
                buying_power = self._client.get_buying_power(
                    symbol,
                    Decimal(str(int(limit_price))),
                    exchange="KRX",
                )
            except Exception as exc:
                raise RuntimeError(
                    "KIS paper buying-power evidence is unavailable"
                ) from None
            raw_broker_cash = min(
                buying_power.orderable_cash,
                buying_power.no_receivable_buy_amount,
            ) - Decimal(minimum_cash_reserve_krw)
            broker_cash_krw = int(
                max(Decimal("0"), raw_broker_cash).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
            broker_quantity = int(buying_power.no_receivable_buy_quantity)
            if quantity > broker_quantity:
                raise ValueError("paper buy exceeds no-receivable broker quantity")
            if quantity * limit_price > broker_cash_krw:
                raise ValueError("paper buy exceeds no-receivable broker cash")
            broker_cash: float | None = float(broker_cash_krw)
        else:
            broker_cash_krw = None
            broker_cash = None
            broker_quantity = None

        quote_basis = _quote_reference_basis(quote)
        fingerprint = _request_fingerprint(
            order_plan=order_plan,
            run_id=run_id,
            user_id=user_id,
            snapshot=snapshot,
            quote=quote,
            quote_reference_basis=quote_basis,
            entry_atr14=entry_atr14,
            broker_orderable_cash=broker_cash,
            broker_orderable_buy_quantity=broker_quantity,
            minimum_cash_reserve_krw=minimum_cash_reserve_krw,
        )
        provenance = self._store.provenance
        submission_evidence_expires_at = min(
            order_plan.risk_check_expires_at,
            quote.as_of + timedelta(seconds=quote_max_age_seconds),
            snapshot.captured_at + timedelta(seconds=snapshot_max_age_seconds),
        )
        dispatch = PaperOrderDispatch(
            order_plan_id=order_plan.order_plan_id,
            run_id=run_id,
            idempotency_key=order_plan.idempotency_key,
            request_fingerprint=fingerprint,
            order_plan_payload=order_plan.model_dump(mode="json"),
            policy_id=order_plan.policy_id,
            policy_version=order_plan.policy_version,
            user_id=user_id,
            strategy_id=explanation.strategy_id,
            strategy_version=explanation.strategy_version,
            purpose=order_plan.purpose,
            symbol=symbol,
            side=order_plan.intent.side,
            quantity=quantity,
            limit_price=limit_price,
            quote_as_of=quote.as_of,
            quote_last=quote.last,
            quote_bid=quote.bid,
            quote_ask=quote.ask,
            quote_reference_basis=quote_basis,
            risk_check_id=order_plan.risk_check_id,
            risk_check_expires_at=order_plan.risk_check_expires_at,
            submission_evidence_expires_at=submission_evidence_expires_at,
            reconciled_snapshot_id=snapshot.snapshot_id,
            reconciled_snapshot_at=snapshot.captured_at,
            snapshot_cash=snapshot.cash,
            snapshot_equity=snapshot.equity,
            snapshot_symbol_quantity=held_quantity,
            snapshot_symbol_orderable_quantity=orderable_quantity,
            snapshot_daily_loss_ratio=snapshot.daily_loss_ratio,
            snapshot_monthly_loss_ratio=snapshot.monthly_loss_ratio,
            broker_orderable_cash=broker_cash,
            broker_orderable_buy_quantity=broker_quantity,
            minimum_cash_reserve_krw=minimum_cash_reserve_krw,
            entry_atr14=entry_atr14,
            store_id=provenance.store_id,
            session_id=self._session.session_id,
            fencing_token=self._session.fencing_token,
            account_scope_fingerprint=client_fingerprint(self._client),
            prepared_at=prepared_at,
            updated_at=prepared_at,
        )
        snapshot_gross_exposure_krw = int(
            max(
                Decimal("0"),
                Decimal(str(snapshot.equity)) - Decimal(str(snapshot.cash)),
            ).to_integral_value(rounding=ROUND_CEILING)
        )
        gross_exposure_limit_krw = int(
            max(
                Decimal("0"),
                Decimal(str(snapshot.equity))
                - Decimal(minimum_cash_reserve_krw),
            ).to_integral_value(rounding=ROUND_FLOOR)
        )
        request_notional_krw = quantity * limit_price
        reservation = PaperRiskReservation(
            order_plan_id=dispatch.order_plan_id,
            idempotency_key=dispatch.idempotency_key,
            kind=("cash_buy" if dispatch.side == "buy" else "sell_quantity"),
            symbol=dispatch.symbol,
            side=dispatch.side,
            reserved_cash_krw=(
                request_notional_krw if dispatch.side == "buy" else None
            ),
            reserved_sell_quantity=(quantity if dispatch.side == "sell" else None),
            reserved_gross_exposure_krw=(
                request_notional_krw if dispatch.side == "buy" else 0
            ),
            broker_orderable_cash_basis_krw=(
                broker_cash_krw if dispatch.side == "buy" else None
            ),
            broker_orderable_buy_quantity_basis=(
                broker_quantity if dispatch.side == "buy" else None
            ),
            snapshot_orderable_quantity_basis=(
                orderable_quantity if dispatch.side == "sell" else None
            ),
            snapshot_gross_exposure_basis_krw=snapshot_gross_exposure_krw,
            minimum_cash_reserve_krw=minimum_cash_reserve_krw,
            gross_exposure_limit_krw=gross_exposure_limit_krw,
            store_id=dispatch.store_id,
            session_id=dispatch.session_id,
            fencing_token=dispatch.fencing_token,
            account_scope_fingerprint=dispatch.account_scope_fingerprint,
            created_at=prepared_at,
            updated_at=prepared_at,
        )
        prepared, _ = self._store.reserve_and_insert_paper_order_dispatch(
            dispatch,
            reservation,
        )
        return prepared

    def submit_prepared_order(
        self,
        order_plan: OrderPlan,
    ) -> tuple[BrokerOrder, list[Fill]]:
        dispatch = self._store.load_paper_order_dispatch(order_plan.order_plan_id)
        if dispatch is None:
            raise RuntimeError("durable paper dispatch must be prepared before submission")
        self._require_order_matches_dispatch(order_plan, dispatch)
        if dispatch.status != "prepared":
            return self._replay_without_post(dispatch)

        now = self._now()
        if self._store.paper_kill_blocks_submission():
            expired = self._terminal_pre_dispatch(
                dispatch,
                status="expired_pre_dispatch",
                error_code="paper_kill_engaged",
                at=now,
            )
            raise PaperPreDispatchFailure(
                expired,
                "paper kill blocks the external dispatch claim",
            )
        if (
            dispatch.session_id != self._session.session_id
            or dispatch.fencing_token != self._session.fencing_token
        ):
            dispatch = self._store.takeover_prepared_paper_order_dispatch(
                dispatch.order_plan_id,
                session=self._session,
                taken_over_at=now,
            )
            now = self._strictly_later(self._now(), dispatch.updated_at)
        if (
            order_plan.risk_check_expires_at is None
            or order_plan.risk_check_expires_at <= now
            or dispatch.submission_evidence_expires_at <= now
        ):
            expired = self._terminal_pre_dispatch(
                dispatch,
                status="expired_pre_dispatch",
                error_code="risk_check_expired",
                at=now,
            )
            raise PaperPreDispatchFailure(
                expired,
                "paper order expired before the external dispatch claim",
            )

        claim_observed_at = self._now()
        try:
            open_session_date = self._session_authority.current_open_session_date(
                claim_observed_at
            )
        except Exception:
            open_session_date = None
        if open_session_date != claim_observed_at.astimezone(KST).date():
            closed = self._terminal_pre_dispatch(
                dispatch,
                status="failed_pre_dispatch",
                error_code="paper_session_closed_before_dispatch",
                at=claim_observed_at,
            )
            raise PaperPreDispatchFailure(
                closed,
                "paper trading session closed before the external dispatch claim",
            )

        claimed = self._store.claim_dispatch_attempt(
            dispatch.order_plan_id,
            session=self._session,
            claimed_at=self._strictly_later(
                claim_observed_at,
                dispatch.updated_at,
            ),
        )
        if self._store.paper_kill_blocks_submission():
            rejected = self._definitive_rejection(
                claimed,
                error_code="paper_kill_engaged_after_claim",
            )
            raise PaperSubmissionRejected(
                rejected,
                "paper kill engaged after claim and before broker POST",
            )
        post_claim_observed_at = self._now()
        try:
            post_claim_session_date = (
                self._session_authority.current_open_session_date(
                    post_claim_observed_at
                )
            )
        except Exception:
            post_claim_session_date = None
        if (
            post_claim_session_date
            != post_claim_observed_at.astimezone(KST).date()
        ):
            rejected = self._definitive_rejection(
                claimed,
                error_code="paper_session_closed_after_claim",
            )
            raise PaperSubmissionRejected(
                rejected,
                "paper trading session closed after claim and before broker POST",
            )
        try:
            result = self._client.place_limit_cash_order(
                symbol=claimed.symbol,
                side=claimed.side,
                quantity=int(claimed.quantity),
                limit_price=Decimal(str(int(claimed.limit_price))),
                exchange="KRX",
            )
        except KisPaperConfigurationError:
            rejected = self._definitive_rejection(
                claimed,
                error_code="local_configuration_error",
            )
            raise PaperSubmissionRejected(
                rejected,
                "paper order was rejected before a usable broker request",
            ) from None
        except KisPaperBusinessError:
            rejected = self._definitive_rejection(
                claimed,
                error_code="broker_business_rejected",
            )
            raise PaperSubmissionRejected(
                rejected,
                "KIS paper explicitly rejected the order",
            ) from None
        except KisPaperOrderOutcomeUnknown:
            unknown = self._outcome_unknown(claimed, "broker_response_ambiguous")
            raise PaperSubmissionOutcomeUnknown(
                unknown,
                "KIS paper order outcome is unknown; reconciliation is required",
            ) from None
        except Exception:
            unknown = self._outcome_unknown(claimed, "broker_exception_after_claim")
            raise PaperSubmissionOutcomeUnknown(
                unknown,
                "paper order raised after claim; reconciliation is required",
            ) from None

        try:
            accepted = self._record_acceptance(claimed, result)
        except Exception:
            current = self._store.load_paper_order_dispatch(claimed.order_plan_id)
            if current is not None and current.status == "dispatch_claimed":
                try:
                    current = self._outcome_unknown(
                        current,
                        "acceptance_persistence_failed",
                    )
                except Exception:
                    pass
            raise PaperSubmissionOutcomeUnknown(
                current or claimed,
                "broker accepted the order but durable acceptance is uncertain",
            ) from None
        return self._evidence_from_dispatch(accepted)

    def _record_acceptance(
        self,
        dispatch: PaperOrderDispatch,
        result: KisCashOrderResult,
    ) -> PaperOrderDispatch:
        if (
            result.symbol != dispatch.symbol
            or result.side != dispatch.side
            or result.quantity != int(dispatch.quantity)
            or result.limit_price != Decimal(str(int(dispatch.limit_price)))
        ):
            return self._outcome_unknown(dispatch, "broker_acceptance_mismatch")
        observed_at = self._now()
        business_date = self._session_authority.current_open_session_date(observed_at)
        if business_date is None:
            return self._outcome_unknown(dispatch, "broker_business_date_unverified")
        updated_at = self._strictly_later(observed_at, dispatch.updated_at)
        accepted = PaperOrderDispatch.model_validate(
            dispatch.model_copy(
                update={
                    "status": "accepted",
                    "broker_business_date": business_date,
                    "broker_order_reference": result.order_number,
                    "broker_forwarding_order_org_number": (
                        result.krx_forwarding_order_org_number
                    ),
                    "broker_order_time": result.order_time,
                    "last_error_code": None,
                    "updated_at": updated_at,
                    "revision": dispatch.revision + 1,
                }
            ).model_dump()
        )
        return self._store.update_paper_order_dispatch(accepted)

    def _definitive_rejection(
        self,
        dispatch: PaperOrderDispatch,
        *,
        error_code: str,
    ) -> PaperOrderDispatch:
        updated_at = self._strictly_later(self._now(), dispatch.updated_at)
        rejected = PaperOrderDispatch.model_validate(
            dispatch.model_copy(
                update={
                    "status": "rejected",
                    "reconciliation_status": "reconciled",
                    "last_error_code": error_code,
                    "updated_at": updated_at,
                    "reconciled_at": updated_at,
                    "revision": dispatch.revision + 1,
                }
            ).model_dump()
        )
        return self._store.update_paper_order_dispatch(rejected)

    def _outcome_unknown(
        self,
        dispatch: PaperOrderDispatch,
        error_code: str,
    ) -> PaperOrderDispatch:
        updated_at = self._strictly_later(self._now(), dispatch.updated_at)
        unknown = PaperOrderDispatch.model_validate(
            dispatch.model_copy(
                update={
                    "status": "outcome_unknown",
                    "last_error_code": error_code,
                    "updated_at": updated_at,
                    "revision": dispatch.revision + 1,
                }
            ).model_dump()
        )
        return self._store.update_paper_order_dispatch(unknown)

    def _terminal_pre_dispatch(
        self,
        dispatch: PaperOrderDispatch,
        *,
        status: str,
        error_code: str,
        at: datetime,
    ) -> PaperOrderDispatch:
        updated_at = self._strictly_later(at, dispatch.updated_at)
        terminal = PaperOrderDispatch.model_validate(
            dispatch.model_copy(
                update={
                    "status": status,
                    "reconciliation_status": "reconciled",
                    "last_error_code": error_code,
                    "updated_at": updated_at,
                    "reconciled_at": updated_at,
                    "revision": dispatch.revision + 1,
                }
            ).model_dump()
        )
        return self._store.update_paper_order_dispatch(terminal)

    def _replay_without_post(
        self,
        dispatch: PaperOrderDispatch,
    ) -> tuple[BrokerOrder, list[Fill]]:
        if dispatch.status in {"dispatch_claimed", "outcome_unknown"}:
            raise PaperSubmissionOutcomeUnknown(
                dispatch,
                "paper order already claimed; reconciliation is required",
            )
        if dispatch.status in {"rejected", "cancelled", "failed_pre_dispatch", "expired_pre_dispatch"}:
            raise PaperSubmissionRejected(
                dispatch,
                "paper dispatch is already terminal and cannot be resent",
            )
        return self._evidence_from_dispatch(dispatch)

    def _evidence_from_dispatch(
        self,
        dispatch: PaperOrderDispatch,
    ) -> tuple[BrokerOrder, list[Fill]]:
        status_map = {
            "accepted": OrderStatus.accepted,
            "partially_filled": OrderStatus.partially_filled,
            "filled": OrderStatus.filled,
        }
        if dispatch.status not in status_map or dispatch.broker_order_reference is None:
            raise PaperSubmissionOutcomeUnknown(
                dispatch,
                "paper dispatch does not contain replayable broker evidence",
            )
        accepted_at = _broker_order_datetime(dispatch)
        broker_order = BrokerOrder(
            broker_order_id=dispatch.broker_order_id,
            order_plan_id=dispatch.order_plan_id,
            broker_mode=BrokerMode.paper,
            status=status_map[dispatch.status],
            accepted_at=accepted_at,
            broker_reference=dispatch.broker_order_reference,
        )
        fills = [
            Fill(
                fill_id=item.broker_fill_reference,
                broker_order_id=dispatch.broker_order_id,
                order_plan_id=dispatch.order_plan_id,
                symbol=dispatch.symbol,
                quantity=item.quantity,
                price=item.price,
                notional=item.notional,
                filled_at=item.evidence_at,
            )
            for item in dispatch.fill_evidence
        ]
        return broker_order, fills

    def _require_preparable_order(
        self,
        order_plan: OrderPlan,
        *,
        snapshot: PortfolioSnapshot,
        quote: Quote,
        prepared_at: datetime,
        quote_max_age_seconds: int,
        snapshot_max_age_seconds: int,
    ) -> None:
        if order_plan.status != OrderStatus.submitted:
            raise ValueError("paper dispatch preparation requires submitted order state")
        if order_plan.intent.order_type.value != "limit":
            raise ValueError("external paper dispatch supports limit orders only")
        if order_plan.risk_check_id is None or order_plan.risk_check_expires_at is None:
            raise ValueError("paper dispatch requires final risk-check evidence")
        if order_plan.risk_check_expires_at <= prepared_at:
            raise ValueError("paper dispatch final risk check is expired")
        if (
            isinstance(quote_max_age_seconds, bool)
            or quote_max_age_seconds <= 0
            or isinstance(snapshot_max_age_seconds, bool)
            or snapshot_max_age_seconds <= 0
        ):
            raise ValueError("paper dispatch freshness limits must be positive")
        if quote.symbol.strip().upper() != order_plan.intent.symbol.strip().upper():
            raise ValueError("paper dispatch quote symbol does not match")
        if quote.as_of > prepared_at or snapshot.captured_at > prepared_at:
            raise ValueError("paper dispatch quote and snapshot cannot be from the future")
        if (prepared_at - quote.as_of).total_seconds() > quote_max_age_seconds:
            raise ValueError("paper dispatch quote is stale")
        if (
            prepared_at - snapshot.captured_at
        ).total_seconds() > snapshot_max_age_seconds:
            raise ValueError("paper dispatch snapshot is stale")
        if snapshot.user_id.strip() == "":
            raise ValueError("paper dispatch snapshot user is required")

    @staticmethod
    def _require_order_matches_dispatch(
        order_plan: OrderPlan,
        dispatch: PaperOrderDispatch,
    ) -> None:
        intent = order_plan.intent
        if (
            dispatch.order_plan_id != order_plan.order_plan_id
            or dispatch.idempotency_key != order_plan.idempotency_key
            or dispatch.policy_id != order_plan.policy_id
            or dispatch.policy_version != order_plan.policy_version
            or dispatch.purpose != order_plan.purpose
            or dispatch.risk_check_id != order_plan.risk_check_id
            or dispatch.risk_check_expires_at != order_plan.risk_check_expires_at
            or dispatch.symbol != intent.symbol.strip().upper()
            or dispatch.side != intent.side
            or not isclose(dispatch.quantity, intent.quantity, abs_tol=0.000001)
            or intent.limit_price is None
            or not isclose(dispatch.limit_price, intent.limit_price, abs_tol=0.000001)
        ):
            raise ValueError("paper order no longer matches its durable dispatch evidence")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("paper coordinator clock must include a UTC offset")
        return value

    @staticmethod
    def _strictly_later(value: datetime, previous: datetime) -> datetime:
        return max(value, previous + timedelta(microseconds=1))


def client_fingerprint(client: KisPaperClient) -> str:
    return client.account_scope_fingerprint


def _whole_positive_number(value: float | None, label: str) -> float:
    if value is None or value <= 0 or not float(value).is_integer():
        raise ValueError(f"KIS paper {label} must be a positive whole number")
    return float(value)


def _whole_nonnegative_number(value: float, label: str) -> int:
    if value < 0 or not float(value).is_integer():
        raise ValueError(f"KIS paper {label} must be a nonnegative whole number")
    return int(value)


def _snapshot_symbol_quantities(
    snapshot: PortfolioSnapshot,
    symbol: str,
) -> tuple[float, float]:
    positions = [
        position
        for position in snapshot.positions
        if position.symbol.strip().upper() == symbol
    ]
    held = sum(position.quantity for position in positions)
    orderable = sum(
        float(
            getattr(
                position,
                "effective_orderable_quantity",
                position.quantity,
            )
        )
        for position in positions
    )
    return held, orderable


def _quote_reference_basis(quote: Quote) -> str:
    if quote.bid is not None and quote.ask is not None and isclose(
        quote.last,
        (quote.bid + quote.ask) / 2,
        abs_tol=0.000001,
    ):
        return "l2_midpoint"
    if quote.bid is not None and isclose(quote.last, quote.bid, abs_tol=0.000001):
        return "best_bid"
    if quote.ask is not None and isclose(quote.last, quote.ask, abs_tol=0.000001):
        return "best_ask"
    raise ValueError("paper Quote requires an explicit L2 reference basis")


def _request_fingerprint(
    *,
    order_plan: OrderPlan,
    run_id: str,
    user_id: str,
    snapshot: PortfolioSnapshot,
    quote: Quote,
    quote_reference_basis: str,
    entry_atr14: float | None,
    broker_orderable_cash: float | None,
    broker_orderable_buy_quantity: float | None,
    minimum_cash_reserve_krw: int,
) -> str:
    payload = {
        "order_plan": order_plan.model_dump(mode="json"),
        "run_id": run_id,
        "user_id": user_id,
        "snapshot": snapshot.model_dump(mode="json"),
        "quote": quote.model_dump(mode="json"),
        "quote_reference_basis": quote_reference_basis,
        "entry_atr14": entry_atr14,
        "broker_orderable_cash": broker_orderable_cash,
        "broker_orderable_buy_quantity": broker_orderable_buy_quantity,
        "minimum_cash_reserve_krw": minimum_cash_reserve_krw,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _broker_order_datetime(dispatch: PaperOrderDispatch) -> datetime:
    if dispatch.broker_business_date is None or dispatch.broker_order_time is None:
        raise ValueError("paper broker evidence is missing date or time")
    raw = dispatch.broker_order_time
    parsed_time = datetime.strptime(raw, "%H%M%S").time()
    return datetime.combine(dispatch.broker_business_date, parsed_time, tzinfo=KST)
