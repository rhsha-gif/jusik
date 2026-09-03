from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from secrets import compare_digest
from typing import Annotated, TypeVar

from fastapi import Header, HTTPException

from quantpilot.packages.core.data.mode import DataModeConfigError
from quantpilot.packages.core.data.providers import ProviderError
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.operator.service import OperatorService
from quantpilot.packages.core.operator.status_snapshot import (
    ProfessionalOperatorStatusSnapshot,
)
from quantpilot.packages.core.schemas import utc_now
from quantpilot.packages.db.paper_status_reader import (
    read_professional_operator_status,
)
from quantpilot.packages.db.repositories import RepositoryRegistry


repositories = RepositoryRegistry()
_harness_service: HarnessService | None = None
_operator_service: OperatorService | None = None
_service_config_key: tuple[str | None, ...] | None = None
T = TypeVar("T")
OPERATOR_SHARED_SECRET_ENV = "QUANTPILOT_OPERATOR_SHARED_SECRET"
OPERATOR_ACTOR_ID_ENV = "QUANTPILOT_OPERATOR_ACTOR_ID"
PAPER_RUNTIME_ROLE = "paper-session"
PAPER_CREDENTIAL_ENV_NAMES = (
    "KIS_PAPER_APP_KEY",
    "KIS_PAPER_APP_SECRET",
    "KIS_PAPER_ACCOUNT_NUMBER",
    "KIS_PAPER_PRODUCT_CODE",
    "KIS_PAPER_ACCESS_TOKEN",
)


def validate_generic_runtime_environment(
    environment: Mapping[str, str] | None = None,
) -> None:
    """Keep paper-session authority out of generic API and smoke processes."""

    env = os.environ if environment is None else environment
    if env.get("QUANTPILOT_RUNTIME_ROLE", "").strip() == PAPER_RUNTIME_ROLE:
        raise RuntimeError("generic_runtime_rejects_paper_session_role")
    paper_arming_present = (
        any(env.get(name, "").strip() for name in PAPER_CREDENTIAL_ENV_NAMES)
        or env.get("BROKER_MODE", "mock").strip().lower() == "paper"
        or env.get("FULLY_AUTOMATED_OPERATOR_ENABLED", "false").strip().lower()
        == "true"
    )
    if paper_arming_present:
        raise RuntimeError("generic_runtime_rejects_paper_arming_environment")


def require_operator_actor(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Authenticate a state-changing API request and return its actor id."""

    expected_secret = os.environ.get(OPERATOR_SHARED_SECRET_ENV)
    configured_actor_id = os.environ.get(OPERATOR_ACTOR_ID_ENV)
    if (
        not expected_secret
        or len(expected_secret) < 32
        or not configured_actor_id
    ):
        raise HTTPException(
            status_code=503,
            detail={"error": "operator actor authentication is not configured"},
        )

    scheme, separator, provided_secret = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not separator
        or not provided_secret
    ):
        raise HTTPException(
            status_code=401,
            detail={"error": "operator actor authentication required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not compare_digest(
        provided_secret.encode("utf-8"),
        expected_secret.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=403,
            detail={"error": "operator actor authentication failed"},
        )

    verified_actor_id = configured_actor_id.strip()
    if (
        not verified_actor_id
        or len(verified_actor_id) > 128
        or not verified_actor_id.isprintable()
    ):
        raise HTTPException(
            status_code=503,
            detail={"error": "operator actor authentication is not configured"},
        )
    return verified_actor_id


def _current_service_config_key() -> tuple[str | None, ...]:
    return (
        os.environ.get("DATA_MODE"),
        os.environ.get("LOCAL_DATA_DIR"),
        os.environ.get("EXTERNAL_HISTORICAL_PROVIDER"),
        os.environ.get("EXTERNAL_HISTORICAL_MARKET"),
        os.environ.get("EXTERNAL_HISTORICAL_SYMBOLS"),
        os.environ.get("EXTERNAL_HISTORICAL_START"),
        os.environ.get("EXTERNAL_HISTORICAL_END"),
        os.environ.get("EXTERNAL_HISTORICAL_HOLIDAYS"),
        os.environ.get("KRX_HOLIDAYS"),
        os.environ.get("EXTERNAL_HISTORICAL_ADJUSTED"),
    )


def _configuration_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "harness data mode configuration invalid",
            "message": str(exc),
            "data_mode": os.environ.get("DATA_MODE", "fixture"),
        },
    )


def _ensure_services() -> HarnessService:
    global _harness_service, _operator_service, _service_config_key

    config_key = _current_service_config_key()
    if _harness_service is None or config_key != _service_config_key:
        try:
            _harness_service = HarnessService.from_environment(repositories)
        except (DataModeConfigError, ProviderError) as exc:
            raise _configuration_error(exc)
        _operator_service = OperatorService(_harness_service)
        _service_config_key = config_key
    return _harness_service


def get_harness_service() -> HarnessService:
    return _ensure_services()


def get_operator_service() -> OperatorService:
    _ensure_services()
    if _operator_service is None:
        raise HTTPException(status_code=503, detail={"error": "operator service is not configured"})
    return _operator_service


def get_professional_operator_status_snapshot(
) -> ProfessionalOperatorStatusSnapshot:
    """Read the durable paper operator through the non-mutating status boundary."""

    return read_professional_operator_status(observed_at=utc_now())


def require_latest(items: Sequence[T], *, resource: str, next_step: str) -> T:
    if not items:
        raise HTTPException(
            status_code=409,
            detail={
                "error": f"no {resource} exists in the current harness session",
                "next_step": next_step,
            },
        )
    return items[-1]
