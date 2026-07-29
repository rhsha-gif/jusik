"""Typed, secret-free canonical events for the KIS paper shadow journal.

The helpers in this module are pure.  They build and validate facts but never
persist state, consult a clock, or call a broker.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any, Literal, Mapping, TypeAlias
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from quantpilot.packages.core.execution.transitions import (
    CANCEL_REQUEST_EVENT_TYPES,
    IMPORT_EVENT_TYPES,
    ORDER_DISPATCH_EVENT_TYPES,
    PAPER_EXECUTION_EVENT_TYPES,
    RISK_RESERVATION_EVENT_TYPES,
    new_dispatch_fills,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperDispatchFillEvidence,
    PaperOrderDispatch,
    PaperRiskReservation,
)
from quantpilot.packages.core.schemas import HarnessModel


KST = ZoneInfo("Asia/Seoul")
SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"

PaperExecutionAggregateType = Literal[
    "order_dispatch",
    "risk_reservation",
    "cancel_request",
]
PaperExecutionSource = Literal[
    "local_prepare",
    "local_dispatch_claim",
    "local_session_takeover",
    "local_submission_result",
    "local_reconciliation_guard",
    "broker_acceptance",
    "broker_reconciliation",
    "process_recovery",
    "kill_cancel",
    "schema_migration",
]
PaperExecutionIdentityKind = Literal[
    "venue_execution",
    "broker_cumulative_delta",
]
PaperMutationOrigin = Literal[
    "broker_post_result",
    "local_submission_guard",
    "local_reconciliation_guard",
    "broker_reconciliation",
    "kill_cancel_journal",
]

PAPER_MUTATION_ORIGIN_SOURCES: dict[str, str] = {
    "broker_post_result": "broker_acceptance",
    "local_submission_guard": "local_submission_result",
    # Local evidence aging out on an already-claimed order. Kept apart from
    # local_submission_guard so ordinary guard code cannot manufacture an
    # outcome_unknown quarantine, and from broker_reconciliation so the audit
    # trail never attributes a local judgement to broker evidence.
    "local_reconciliation_guard": "local_reconciliation_guard",
    "broker_reconciliation": "broker_reconciliation",
    "kill_cancel_journal": "kill_cancel",
}


class PaperEventError(ValueError):
    """Base class for pure canonical-event failures."""


class PaperEventStreamConflict(PaperEventError):
    """A valid fact conflicts with the current stream or identity scope."""


class PaperEventStreamCorruption(PaperEventError):
    """Persisted or supplied event bytes violate their canonical envelope."""


class PaperEventSchemaUnsupported(PaperEventError):
    """The raw event schema or semantic type is not supported by v1."""


class PaperExecutionEventProvenance(HarnessModel):
    store_id: str
    account_scope_fingerprint: str = Field(pattern=SHA256_PATTERN)
    data_mode: Literal["paper_trading"] = "paper_trading"
    broker_environment: Literal["kis_paper"] = "kis_paper"

    @field_validator("store_id")
    @classmethod
    def store_id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("event provenance store_id must not be blank")
        return normalized


class PaperOrderDispatchEventPayload(HarnessModel):
    after: PaperOrderDispatch
    legacy_snapshot: Literal[True] | None = None


class PaperRiskReservationEventPayload(HarnessModel):
    after: PaperRiskReservation
    legacy_snapshot: Literal[True] | None = None


class PaperCancelRequestEventPayload(HarnessModel):
    after: PaperCancelRequest
    legacy_snapshot: Literal[True] | None = None


PaperExecutionPayload: TypeAlias = (
    PaperOrderDispatchEventPayload
    | PaperRiskReservationEventPayload
    | PaperCancelRequestEventPayload
)
PaperExecutionAfter: TypeAlias = (
    PaperOrderDispatch | PaperRiskReservation | PaperCancelRequest
)


class PaperExecutionIdentityKey(HarnessModel):
    kind: PaperExecutionIdentityKind
    external_id: str
    scope_hash: str = Field(pattern=SHA256_PATTERN)
    evidence_payload_hash: str = Field(pattern=SHA256_PATTERN)

    @field_validator("external_id")
    @classmethod
    def external_id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("execution identity external_id must not be blank")
        return normalized


_SECRET_FIELD_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "app_key",
    "appkey",
    "app_secret",
    "appsecret",
    "authorization",
    "bearer_token",
    "client_secret",
    "password",
    "refresh_token",
    "secret",
    "token",
}
_RAW_ACCOUNT_FIELD_NAMES = {
    "account",
    "account_id",
    "account_no",
    "account_number",
    "account_product_code",
    "acnt_prdt_cd",
    "cano",
}


def _normalized_key(key: object) -> str:
    return str(key).replace("-", "_").strip().lower()


def _is_secret_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return (
        normalized in _SECRET_FIELD_NAMES
        or normalized.endswith("_secret")
        or normalized.endswith("_token")
        or normalized.endswith("_app_key")
        or normalized.endswith("_app_secret")
        or normalized.endswith("_access_token")
        or normalized.endswith("_password")
        or normalized.endswith("_api_key")
    )


def _walk_for_secrets(value: Any, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, BaseModel):
        _walk_for_secrets(value.model_dump(mode="python"), path=path)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalized_key(key)
            item_path = (*path, normalized)
            if normalized == "fencing_token":
                if item_path != ("payload", "after", "fencing_token") or (
                    isinstance(item, bool) or not isinstance(item, int) or item < 1
                ):
                    raise ValueError("fencing_token is allowed only as a typed session fence")
            elif _is_secret_key(key) or normalized in _RAW_ACCOUNT_FIELD_NAMES:
                raise ValueError(f"canonical event contains forbidden field: {normalized}")
            _walk_for_secrets(item, path=item_path)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _walk_for_secrets(item, path=path)
        return
    if isinstance(value, str) and value.lstrip().lower().startswith("bearer "):
        raise ValueError("canonical event contains a bearer credential")


def _require_aware(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value


def _payload_canonical_object(payload: PaperExecutionPayload) -> dict[str, Any]:
    result: dict[str, Any] = {"after": payload.after.model_dump(mode="json")}
    if payload.legacy_snapshot is True:
        result["legacy_snapshot"] = True
    return result


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a typed preimage using the contract's single JSON rule."""

    if isinstance(value, (PaperOrderDispatchEventPayload, PaperRiskReservationEventPayload, PaperCancelRequestEventPayload)):
        value = _payload_canonical_object(value)
    elif isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def canonical_import_event_id(
    *,
    store_id: str,
    aggregate_type: PaperExecutionAggregateType,
    aggregate_id: str,
    source_revision: int,
    payload_hash: str,
) -> str:
    preimage = {
        "aggregate_id": aggregate_id,
        "aggregate_type": aggregate_type,
        "event_schema_version": 1,
        "payload_hash": payload_hash,
        "source_revision": source_revision,
        "store_id": store_id,
    }
    return f"pevt_import_{canonical_sha256(preimage).removeprefix('sha256:')}"


def _identity_scope_hash(
    *,
    after: PaperOrderDispatch,
    evidence: PaperDispatchFillEvidence,
    kind: PaperExecutionIdentityKind,
) -> str:
    if kind == "venue_execution":
        preimage = {
            "account_scope_fingerprint": after.account_scope_fingerprint,
            "broker_environment": after.broker_environment,
            "external_id": evidence.broker_fill_reference,
            "identity_kind": kind,
        }
    else:
        business_date = after.broker_business_date
        broker_order_reference = after.broker_order_reference
        broker_order_branch_number = after.broker_order_branch_number
        if (
            business_date is None
            or broker_order_reference is None
            or broker_order_branch_number is None
        ):
            raise PaperEventStreamCorruption(
                "cumulative fill identity lacks exact KIS order scope"
            )
        preimage = {
            "account_scope_fingerprint": after.account_scope_fingerprint,
            "broker_business_date": business_date.isoformat(),
            "broker_environment": after.broker_environment,
            "broker_order_branch_number": broker_order_branch_number,
            "broker_order_reference": broker_order_reference,
            "external_id": evidence.broker_fill_reference,
            "identity_kind": kind,
        }
    return canonical_sha256(preimage)


def identity_keys_for_dispatch(
    before: PaperOrderDispatch | None,
    after: PaperOrderDispatch,
) -> tuple[PaperExecutionIdentityKey, ...]:
    keys: list[PaperExecutionIdentityKey] = []
    for evidence in new_dispatch_fills(before, after):
        kind: PaperExecutionIdentityKind = (
            "venue_execution"
            if evidence.time_basis == "broker_execution"
            else "broker_cumulative_delta"
        )
        keys.append(
            PaperExecutionIdentityKey(
                kind=kind,
                external_id=evidence.broker_fill_reference,
                scope_hash=_identity_scope_hash(
                    after=after,
                    evidence=evidence,
                    kind=kind,
                ),
                evidence_payload_hash=canonical_sha256(evidence),
            )
        )
    return tuple(sorted(keys, key=lambda item: (item.kind, item.scope_hash)))


def payload_after(payload: PaperExecutionPayload) -> PaperExecutionAfter:
    return payload.after


def _expected_envelope_identity(
    after: PaperExecutionAfter,
) -> dict[str, Any]:
    if isinstance(after, PaperOrderDispatch):
        return {
            "aggregate_type": "order_dispatch",
            "aggregate_id": after.order_plan_id,
            "correlation_id": after.order_plan_id,
            "idempotency_key": after.idempotency_key,
            "local_broker_order_id": after.broker_order_id,
            "broker_order_id": after.broker_order_reference,
        }
    if isinstance(after, PaperRiskReservation):
        return {
            "aggregate_type": "risk_reservation",
            "aggregate_id": after.reservation_id,
            "correlation_id": after.order_plan_id,
            "idempotency_key": after.idempotency_key,
            "local_broker_order_id": None,
            "broker_order_id": None,
        }
    return {
        "aggregate_type": "cancel_request",
        "aggregate_id": after.cancel_id,
        "correlation_id": after.order_plan_id,
        "idempotency_key": None,
        "local_broker_order_id": after.broker_order_id,
        "broker_order_id": after.broker_order_reference,
    }


def _expected_event_time(event_type: str, after: PaperExecutionAfter) -> datetime:
    if (
        event_type == "OrderAccepted"
        and isinstance(after, PaperOrderDispatch)
        and after.broker_business_date is not None
        and after.broker_order_time is not None
    ):
        parsed_time = datetime.strptime(after.broker_order_time, "%H%M%S").time()
        return datetime.combine(after.broker_business_date, parsed_time, tzinfo=KST)
    return after.updated_at


class PaperExecutionEvent(HarnessModel):
    event_id: str
    event_schema_version: Literal[1] = 1
    aggregate_type: PaperExecutionAggregateType
    aggregate_id: str
    aggregate_version: int = Field(ge=1)
    event_type: str
    store_id: str
    account_scope_fingerprint: str = Field(pattern=SHA256_PATTERN)
    data_mode: Literal["paper_trading"] = "paper_trading"
    broker_environment: Literal["kis_paper"] = "kis_paper"
    source: PaperExecutionSource
    occurred_at: datetime
    received_at: datetime
    correlation_id: str
    causation_id: str | None = None
    idempotency_key: str | None = None
    local_broker_order_id: str | None = None
    broker_order_id: str | None = None
    original_client_order_id: str | None = None
    venue_order_id: str | None = None
    identity_keys: list[PaperExecutionIdentityKey] = Field(default_factory=list)
    broker_sequence: int | None = Field(default=None, ge=0)
    source_revision: int = Field(ge=0)
    payload: PaperExecutionPayload
    payload_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="before")
    @classmethod
    def event_must_be_secret_free(cls, value: Any) -> Any:
        _walk_for_secrets(value)
        return value

    @field_validator(
        "event_id",
        "aggregate_id",
        "store_id",
        "correlation_id",
        "causation_id",
        "idempotency_key",
        "local_broker_order_id",
        "broker_order_id",
        "original_client_order_id",
        "venue_order_id",
    )
    @classmethod
    def identifiers_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("canonical event identifiers must not be blank")
        return normalized

    @field_validator("occurred_at", "received_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, field_name="event timestamp")

    @field_validator("identity_keys")
    @classmethod
    def identity_keys_must_be_canonical(
        cls,
        value: list[PaperExecutionIdentityKey],
    ) -> list[PaperExecutionIdentityKey]:
        ordered = sorted(value, key=lambda item: (item.kind, item.scope_hash))
        scopes = [item.scope_hash for item in ordered]
        if len(scopes) != len(set(scopes)):
            raise ValueError("canonical event identity scopes must be unique")
        if value != ordered:
            raise ValueError("canonical event identity keys must be sorted")
        return value

    @model_validator(mode="after")
    def envelope_must_match_payload(self) -> "PaperExecutionEvent":
        if self.event_type not in PAPER_EXECUTION_EVENT_TYPES:
            raise ValueError("unknown canonical event type")
        after = payload_after(self.payload)
        expected = _expected_envelope_identity(after)
        for name, value in expected.items():
            if getattr(self, name) != value:
                raise ValueError(f"canonical event {name} does not match after-state")
        if self.source_revision != after.revision:
            raise ValueError("canonical event source revision does not match after-state")
        if (
            self.store_id != after.store_id
            or self.account_scope_fingerprint != after.account_scope_fingerprint
            or self.data_mode != after.data_mode
            or self.broker_environment != after.broker_environment
        ):
            raise ValueError("canonical event provenance does not match after-state")
        if self.original_client_order_id is not None or self.venue_order_id is not None:
            raise ValueError("v1 cannot assert replace or venue order identity")
        if self.broker_sequence is not None:
            raise ValueError("v1 current KIS evidence cannot assert broker sequence")
        expected_payload_hash = canonical_sha256(self.payload)
        if self.payload_hash != expected_payload_hash:
            raise ValueError("canonical event payload hash mismatch")
        expected_type_set = {
            "order_dispatch": ORDER_DISPATCH_EVENT_TYPES,
            "risk_reservation": RISK_RESERVATION_EVENT_TYPES,
            "cancel_request": CANCEL_REQUEST_EVENT_TYPES,
        }[self.aggregate_type]
        if self.event_type not in expected_type_set:
            raise ValueError("canonical event type does not match aggregate")
        is_import = self.event_type in IMPORT_EVENT_TYPES
        if is_import != (self.payload.legacy_snapshot is True):
            raise ValueError("legacy_snapshot must appear only on import events")
        if is_import:
            if self.source != "schema_migration" or self.occurred_at != after.updated_at:
                raise ValueError("legacy import source or occurred_at is invalid")
            expected_import_id = canonical_import_event_id(
                store_id=self.store_id,
                aggregate_type=self.aggregate_type,
                aggregate_id=self.aggregate_id,
                source_revision=self.source_revision,
                payload_hash=self.payload_hash,
            )
            if self.event_id != expected_import_id:
                raise ValueError("legacy import event_id is not deterministic")
        else:
            if self.source == "schema_migration":
                raise ValueError("runtime event cannot use migration source")
            if self.received_at != after.updated_at:
                raise ValueError("runtime event received_at must equal durable update time")
            if self.occurred_at != _expected_event_time(self.event_type, after):
                raise ValueError("runtime event occurred_at is not store-derived")
        if isinstance(after, PaperOrderDispatch):
            fill_by_id = {
                item.broker_fill_reference: item for item in after.fill_evidence
            }
            for key in self.identity_keys:
                evidence = fill_by_id.get(key.external_id)
                if evidence is None:
                    raise ValueError("event identity key lacks matching fill evidence")
                expected_kind = (
                    "venue_execution"
                    if evidence.time_basis == "broker_execution"
                    else "broker_cumulative_delta"
                )
                if (
                    key.kind != expected_kind
                    or key.scope_hash
                    != _identity_scope_hash(after=after, evidence=evidence, kind=expected_kind)
                    or key.evidence_payload_hash != canonical_sha256(evidence)
                ):
                    raise ValueError("event identity key does not match fill evidence")
            if is_import and tuple(self.identity_keys) != identity_keys_for_dispatch(None, after):
                raise ValueError("dispatch import must index every legacy fill")
        elif self.identity_keys:
            raise ValueError("only order dispatch events can introduce fill identities")
        return self


def event_canonical_bytes(event: PaperExecutionEvent) -> bytes:
    return canonical_json_bytes(event)


def event_fingerprint(event: PaperExecutionEvent) -> str:
    return canonical_sha256(event)


def decode_paper_execution_event(
    value: PaperExecutionEvent | Mapping[str, Any],
) -> PaperExecutionEvent:
    """Classify unknown schema/type before strict Pydantic construction."""

    if isinstance(value, PaperExecutionEvent):
        try:
            return PaperExecutionEvent.model_validate(value.model_dump())
        except ValidationError as exc:
            raise PaperEventStreamCorruption("malformed canonical event") from exc
    if not isinstance(value, Mapping):
        raise PaperEventStreamCorruption("canonical event must be an object")
    schema = value.get("event_schema_version")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != 1:
        raise PaperEventSchemaUnsupported("unsupported canonical event schema")
    event_type = value.get("event_type")
    if not isinstance(event_type, str) or event_type not in PAPER_EXECUTION_EVENT_TYPES:
        raise PaperEventSchemaUnsupported("unsupported canonical event type")
    try:
        return PaperExecutionEvent.model_validate(value)
    except ValueError as exc:
        raise PaperEventStreamCorruption("malformed canonical event") from exc


def build_paper_execution_event(
    *,
    event_id: str,
    aggregate_version: int,
    event_type: str,
    source: PaperExecutionSource,
    after: PaperExecutionAfter,
    causation_id: str | None,
    before: PaperOrderDispatch | None = None,
    legacy_snapshot: bool = False,
    migration_received_at: datetime | None = None,
) -> PaperExecutionEvent:
    """Build one store-candidate fact from validated authoritative models."""

    identity = _expected_envelope_identity(after)
    if isinstance(after, PaperOrderDispatch):
        payload: PaperExecutionPayload = PaperOrderDispatchEventPayload(
            after=after,
            legacy_snapshot=True if legacy_snapshot else None,
        )
        identity_keys = list(identity_keys_for_dispatch(before, after))
    elif isinstance(after, PaperRiskReservation):
        payload = PaperRiskReservationEventPayload(
            after=after,
            legacy_snapshot=True if legacy_snapshot else None,
        )
        identity_keys = []
    else:
        payload = PaperCancelRequestEventPayload(
            after=after,
            legacy_snapshot=True if legacy_snapshot else None,
        )
        identity_keys = []
    if legacy_snapshot:
        if migration_received_at is None:
            raise ValueError("legacy import requires one migration received_at")
        received_at = _require_aware(
            migration_received_at,
            field_name="migration_received_at",
        )
        occurred_at = after.updated_at
    else:
        if migration_received_at is not None:
            raise ValueError("runtime event cannot accept a migration clock")
        received_at = after.updated_at
        occurred_at = _expected_event_time(event_type, after)
    return PaperExecutionEvent(
        event_id=event_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        store_id=after.store_id,
        account_scope_fingerprint=after.account_scope_fingerprint,
        data_mode=after.data_mode,
        broker_environment=after.broker_environment,
        source=source,
        occurred_at=occurred_at,
        received_at=received_at,
        causation_id=causation_id,
        identity_keys=identity_keys,
        source_revision=after.revision,
        payload=payload,
        payload_hash=canonical_sha256(payload),
        **identity,
    )
