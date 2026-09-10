"""Pure strategy-lab contracts and a fail-closed Docker signal runner.

Lead integration API
--------------------
``Candidate.create(...)`` establishes immutable source/version identity.
``register_generated``, ``move_to_shadow``, ``move_to_paper_trial`` and
``replace_source`` are pure transitions returning ``LabState`` or ``LabError``.
``evaluate_promotion(candidate, trades, tests, review, now)`` accepts only
hash/version-bound, trusted forward evidence.  ``run_candidate_generation``
and ``run_candidate_review`` return AI source/review as data; they never execute
it.  ``verify_sandbox_readiness`` must succeed before ``run_signal_code`` can
execute candidate code in a pinned, no-network Docker container.

Signal-runner output is advisory ``SignalSuggestion`` data and cannot encode
quantity, price, order type, approval, or submission authority.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import uuid4
from typing import Any, Callable, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

Provider = Literal["claude", "codex"]
AdapterRunner = Callable[[str, str, dict[str, Any]], dict[str, Any]]
SubprocessRunner = Callable[..., Any]
MAX_SHADOW_CANDIDATES = 3
MAX_PAPER_TRIALS = 1
MAX_GENERATED_PER_DAY = 1
FIRST_PAPER_TRIAL_CAP = 0.10
MIN_FORWARD_TRADES = 20
MIN_FORWARD_SESSIONS = 5
MAX_SOURCE_BYTES = 262_144
KRX_TIMEZONE = timezone(timedelta(hours=9))


def source_sha256(source: str) -> str:
    """Return the canonical UTF-8 SHA-256 identity for candidate source."""

    if not isinstance(source, str) or not source.strip() or "\x00" in source:
        raise ValueError("source must be non-empty text without NUL")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds bounded size")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _require_now(now: datetime) -> None:
    if not _aware(now):
        raise ValueError("now must be timezone-aware")


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        if not _aware(value):
            raise TypeError("naive datetime")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError("unsupported value")


class LabErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    DAILY_GENERATION_LIMIT = "daily_generation_limit"
    SHADOW_LIMIT = "shadow_limit"
    PAPER_TRIAL_LIMIT = "paper_trial_limit"
    PROMOTION_BLOCKED = "promotion_blocked"
    INDEPENDENCE_REQUIRED = "independence_required"
    RUNNER_FAILED = "runner_failed"
    MALFORMED_OUTPUT = "malformed_output"
    SANDBOX_DISABLED = "sandbox_disabled"
    SANDBOX_NOT_READY = "sandbox_not_ready"
    SANDBOX_CONFIGURATION = "sandbox_configuration"
    SANDBOX_TIMEOUT = "sandbox_timeout"
    SANDBOX_OUTPUT_LIMIT = "sandbox_output_limit"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LabError(StrictModel):
    code: LabErrorCode
    reasons: tuple[str, ...] = ()


class Candidate(StrictModel):
    """Immutable generated-code identity and lead-persistable lifecycle state."""

    candidate_id: str
    strategy_id: str
    version: str
    source: str
    source_hash: str
    generator_provider: Provider
    generated_at: datetime
    stage: Literal["generated", "shadow", "paper_trial", "promoted", "rejected"] = (
        "generated"
    )
    allocation_cap: float = 0.0

    @field_validator("candidate_id", "strategy_id", "version")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must be non-empty")
        return value

    @field_validator("generated_at")
    @classmethod
    def _generated_at_aware(cls, value: datetime) -> datetime:
        if not _aware(value):
            raise ValueError("generated_at must be timezone-aware")
        return value

    @field_validator("allocation_cap")
    @classmethod
    def _finite_cap(cls, value: float) -> float:
        if not math.isfinite(value) or not 0.0 <= value <= FIRST_PAPER_TRIAL_CAP:
            raise ValueError("allocation cap must be finite and at most 0.10")
        return value

    @model_validator(mode="after")
    def _identity_and_stage(self) -> "Candidate":
        if self.source_hash != source_sha256(self.source):
            raise ValueError("source hash mismatch")
        if (
            self.stage == "paper_trial"
            and not 0.0 < self.allocation_cap <= FIRST_PAPER_TRIAL_CAP
        ):
            raise ValueError("paper trial requires a positive capped allocation")
        if self.stage != "paper_trial" and self.allocation_cap != 0.0:
            raise ValueError("only a paper trial may have an allocation cap")
        return self

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        strategy_id: str,
        version: str,
        source: str,
        generator_provider: Provider,
        generated_at: datetime,
    ) -> "Candidate":
        return cls(
            candidate_id=candidate_id,
            strategy_id=strategy_id,
            version=version,
            source=source,
            source_hash=source_sha256(source),
            generator_provider=generator_provider,
            generated_at=generated_at,
        )


class ForwardTradeEvidence(StrictModel):
    """Trusted, externally calculated cost-adjusted CLOSED trade evidence."""

    trade_id: str
    session_date: date
    closed_at: datetime
    state: Literal["CLOSED"]
    cost_adjusted_net: float
    source_hash: str
    candidate_version: str
    venue: Literal["KRX"] = "KRX"
    provenance: Literal["trusted_external"]

    @field_validator("trade_id", "source_hash", "candidate_version")
    @classmethod
    def _trade_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trade identity fields must be non-empty")
        return value

    @field_validator("closed_at")
    @classmethod
    def _closed_at_aware(cls, value: datetime) -> datetime:
        if not _aware(value):
            raise ValueError("closed_at must be timezone-aware")
        return value

    @field_validator("cost_adjusted_net")
    @classmethod
    def _finite_net(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("cost-adjusted net must be finite")
        return value

    @field_validator("session_date")
    @classmethod
    def _weekday_session(cls, value: date) -> date:
        if value.weekday() >= 5:
            raise ValueError("KRX session date must be a weekday")
        return value

    @model_validator(mode="after")
    def _session_matches_close(self) -> "ForwardTradeEvidence":
        if self.closed_at.astimezone(KRX_TIMEZONE).date() != self.session_date:
            raise ValueError("session date must match close time in KRX timezone")
        return self


class VerificationEvidence(StrictModel):
    kind: Literal["safety", "reproducibility", "no_lookahead"]
    passed: Literal[True]
    source_hash: str
    candidate_version: str
    observed_at: datetime
    verifier: str
    provenance: Literal["trusted_test_runner"]

    @field_validator("source_hash", "candidate_version", "verifier")
    @classmethod
    def _verification_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("verification fields must be non-empty")
        return value

    @field_validator("observed_at")
    @classmethod
    def _verified_at_aware(cls, value: datetime) -> datetime:
        if not _aware(value):
            raise ValueError("observed_at must be timezone-aware")
        return value


class IndependentReview(StrictModel):
    reviewer_provider: Provider
    generator_provider: Provider
    approved: bool
    source_hash: str
    candidate_version: str
    observed_at: datetime
    summary: str

    @field_validator("source_hash", "candidate_version", "summary")
    @classmethod
    def _review_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review fields must be non-empty")
        return value

    @field_validator("observed_at")
    @classmethod
    def _review_time(cls, value: datetime) -> datetime:
        if not _aware(value):
            raise ValueError("observed_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _independent(self) -> "IndependentReview":
        if self.reviewer_provider == self.generator_provider:
            raise ValueError("reviewer must be independent from generator")
        return self


class PromotionDecision(StrictModel):
    eligible: bool
    reasons: tuple[str, ...]
    source_hash: str
    candidate_version: str
    first_paper_trial_cap: float = 0.0

    @model_validator(mode="after")
    def _consistent_decision(self) -> "PromotionDecision":
        if not self.source_hash.strip() or not self.candidate_version.strip():
            raise ValueError("promotion identity must be non-empty")
        expected = FIRST_PAPER_TRIAL_CAP if self.eligible else 0.0
        if self.first_paper_trial_cap != expected:
            raise ValueError("promotion cap does not match eligibility")
        if self.eligible and self.reasons:
            raise ValueError("eligible promotion cannot contain blockers")
        return self


def evaluate_promotion(
    candidate: Candidate,
    trades: tuple[ForwardTradeEvidence, ...] | list[ForwardTradeEvidence],
    tests: tuple[VerificationEvidence, ...] | list[VerificationEvidence],
    review: IndependentReview,
    now: datetime,
) -> PromotionDecision:
    """Evaluate promotion solely from trusted, forward, identity-bound evidence."""

    _require_now(now)
    reasons: list[str] = []
    if candidate.generated_at > now:
        reasons.append("future_candidate")
    if review.observed_at > now:
        reasons.append("future_review")
    if review.observed_at < candidate.generated_at:
        reasons.append("pre_generation_review")
    if (
        review.source_hash != candidate.source_hash
        or review.candidate_version != candidate.version
    ):
        reasons.append("review_identity_mismatch")
    if (
        review.generator_provider != candidate.generator_provider
        or review.reviewer_provider == candidate.generator_provider
    ):
        reasons.append("review_not_independent")
    if not review.approved:
        reasons.append("review_not_approved")

    trade_ids = [trade.trade_id for trade in trades]
    if len(trades) < MIN_FORWARD_TRADES or len(set(trade_ids)) != len(trade_ids):
        reasons.append("insufficient_unique_closed_trades")
    sessions = {trade.session_date for trade in trades}
    if len(sessions) < MIN_FORWARD_SESSIONS:
        reasons.append("insufficient_forward_sessions")
    for trade in trades:
        if (
            trade.closed_at > now
            or trade.session_date > now.astimezone(trade.closed_at.tzinfo).date()
        ):
            reasons.append("future_trade_evidence")
        if trade.closed_at < candidate.generated_at:
            reasons.append("pre_generation_trade_evidence")
        if (
            trade.source_hash != candidate.source_hash
            or trade.candidate_version != candidate.version
        ):
            reasons.append("trade_identity_mismatch")
    total_net = sum(trade.cost_adjusted_net for trade in trades)
    if not math.isfinite(total_net) or total_net <= 0.0:
        reasons.append("nonpositive_cost_adjusted_net")

    required = {"safety", "reproducibility", "no_lookahead"}
    present: set[str] = set()
    for item in tests:
        if item.observed_at > now:
            reasons.append("future_test_evidence")
        if item.observed_at < candidate.generated_at:
            reasons.append("pre_generation_test_evidence")
        if (
            item.source_hash != candidate.source_hash
            or item.candidate_version != candidate.version
        ):
            reasons.append("test_identity_mismatch")
        if item.verifier.casefold() in {
            candidate.generator_provider,
            "self",
            "candidate",
            "ai",
        }:
            reasons.append("self_attestation")
        present.add(item.kind)
    if present != required:
        reasons.append("missing_required_tests")

    unique_reasons = tuple(dict.fromkeys(reasons))
    eligible = not unique_reasons
    return PromotionDecision(
        eligible=eligible,
        reasons=unique_reasons,
        source_hash=candidate.source_hash,
        candidate_version=candidate.version,
        first_paper_trial_cap=FIRST_PAPER_TRIAL_CAP if eligible else 0.0,
    )


class LabState(StrictModel):
    candidates: tuple[Candidate, ...] = ()


def _replace(state: LabState, candidate: Candidate) -> LabState:
    return LabState(
        candidates=tuple(
            candidate if item.candidate_id == candidate.candidate_id else item
            for item in state.candidates
        )
    )


def register_generated(
    state: LabState, candidate: Candidate, now: datetime
) -> LabState | LabError:
    """Register at most one newly generated candidate per local calendar day."""

    try:
        _require_now(now)
    except ValueError:
        return LabError(code=LabErrorCode.INVALID_INPUT)
    if candidate.stage != "generated" or candidate.generated_at > now:
        return LabError(code=LabErrorCode.INVALID_INPUT)
    if any(item.candidate_id == candidate.candidate_id for item in state.candidates):
        return LabError(code=LabErrorCode.INVALID_INPUT)
    local_day = candidate.generated_at.date()
    if any(
        item.generated_at.astimezone(candidate.generated_at.tzinfo).date() == local_day
        for item in state.candidates
    ):
        return LabError(code=LabErrorCode.DAILY_GENERATION_LIMIT)
    return LabState(candidates=(*state.candidates, candidate))


def move_to_shadow(state: LabState, candidate_id: str) -> LabState | LabError:
    """Move one generated candidate to shadow while enforcing the cap of three."""

    target = next(
        (item for item in state.candidates if item.candidate_id == candidate_id), None
    )
    if target is None or target.stage != "generated":
        return LabError(code=LabErrorCode.INVALID_INPUT)
    if (
        sum(item.stage == "shadow" for item in state.candidates)
        >= MAX_SHADOW_CANDIDATES
    ):
        return LabError(code=LabErrorCode.SHADOW_LIMIT)
    return _replace(state, target.model_copy(update={"stage": "shadow"}))


def move_to_paper_trial(
    state: LabState,
    candidate_id: str,
    decision: PromotionDecision,
    allocation_cap: float = FIRST_PAPER_TRIAL_CAP,
) -> LabState | LabError:
    """Start the sole paper trial, capped at ten percent, after promotion evidence."""

    target = next(
        (item for item in state.candidates if item.candidate_id == candidate_id), None
    )
    if target is None or target.stage != "shadow":
        return LabError(code=LabErrorCode.INVALID_INPUT)
    if not decision.eligible:
        return LabError(code=LabErrorCode.PROMOTION_BLOCKED, reasons=decision.reasons)
    if (
        decision.source_hash != target.source_hash
        or decision.candidate_version != target.version
    ):
        return LabError(
            code=LabErrorCode.PROMOTION_BLOCKED, reasons=("decision_identity_mismatch",)
        )
    if decision.first_paper_trial_cap != FIRST_PAPER_TRIAL_CAP:
        return LabError(
            code=LabErrorCode.PROMOTION_BLOCKED, reasons=("invalid_decision_cap",)
        )
    if (
        sum(item.stage == "paper_trial" for item in state.candidates)
        >= MAX_PAPER_TRIALS
    ):
        return LabError(code=LabErrorCode.PAPER_TRIAL_LIMIT)
    if isinstance(allocation_cap, bool) or not isinstance(allocation_cap, (int, float)):
        return LabError(code=LabErrorCode.INVALID_INPUT)
    cap = float(allocation_cap)
    if not math.isfinite(cap) or not 0.0 < cap <= FIRST_PAPER_TRIAL_CAP:
        return LabError(code=LabErrorCode.INVALID_INPUT)
    return _replace(
        state, target.model_copy(update={"stage": "paper_trial", "allocation_cap": cap})
    )


def replace_source(
    candidate: Candidate,
    *,
    source: str,
    version: str,
    generator_provider: Provider,
    generated_at: datetime,
) -> Candidate | LabError:
    """Create a new generated identity; all old hash-bound evidence is reset."""

    if version == candidate.version:
        return LabError(
            code=LabErrorCode.INVALID_INPUT, reasons=("version_must_change",)
        )
    try:
        return Candidate.create(
            candidate_id=candidate.candidate_id,
            strategy_id=candidate.strategy_id,
            version=version,
            source=source,
            generator_provider=generator_provider,
            generated_at=generated_at,
        )
    except (TypeError, ValueError):
        return LabError(code=LabErrorCode.INVALID_INPUT)


class CandidateSource(StrictModel):
    provider: Provider
    model: str
    strategy_id: str
    version: str
    source: str
    source_hash: str
    generated_at: datetime

    @field_validator("model", "strategy_id", "version")
    @classmethod
    def _source_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("generated identity fields must be non-empty")
        return value

    @field_validator("generated_at")
    @classmethod
    def _source_time(cls, value: datetime) -> datetime:
        if not _aware(value):
            raise ValueError("generated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _source_identity(self) -> "CandidateSource":
        if self.source_hash != source_sha256(self.source):
            raise ValueError("source hash mismatch")
        return self


class _GeneratedContent(StrictModel):
    model: str
    strategy_id: str
    version: str
    source: str

    @field_validator("model", "strategy_id", "version", "source")
    @classmethod
    def _generated_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("generated fields must be non-empty")
        return value


class _ReviewContent(StrictModel):
    model: str
    approved: bool
    summary: str

    @field_validator("model", "summary")
    @classmethod
    def _review_content_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review fields must be non-empty")
        return value


_GENERATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "model": {"type": "string", "minLength": 1},
        "strategy_id": {"type": "string", "minLength": 1},
        "version": {"type": "string", "minLength": 1},
        "source": {"type": "string", "minLength": 1},
    },
    "required": ["model", "strategy_id", "version", "source"],
}

_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "model": {"type": "string", "minLength": 1},
        "approved": {"type": "boolean"},
        "summary": {"type": "string", "minLength": 1},
    },
    "required": ["model", "approved", "summary"],
}


def run_candidate_generation(
    evidence: dict[str, Any], now: datetime, provider: Provider, runner: AdapterRunner
) -> CandidateSource | LabError:
    """Return generated Python source as inert data; never import or execute it."""

    if provider not in ("claude", "codex"):
        return LabError(code=LabErrorCode.INVALID_INPUT)
    try:
        _require_now(now)
        prompt = (
            "Return deterministic signal-function source only as JSON data. No orders, broker APIs, tools, or I/O.\n"
            + json.dumps(
                evidence,
                allow_nan=False,
                default=_json_default,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError):
        return LabError(code=LabErrorCode.INVALID_INPUT)
    try:
        content = _GeneratedContent.model_validate(
            runner(provider, prompt, _GENERATION_SCHEMA)
        )
        digest = source_sha256(content.source)
        return CandidateSource(
            provider=provider,
            model=content.model,
            strategy_id=content.strategy_id,
            version=content.version,
            source=content.source,
            source_hash=digest,
            generated_at=now,
        )
    except (ValidationError, TypeError, ValueError):
        return LabError(code=LabErrorCode.MALFORMED_OUTPUT)
    except Exception:
        return LabError(code=LabErrorCode.RUNNER_FAILED)


def run_candidate_review(
    candidate: Candidate,
    evidence: dict[str, Any],
    now: datetime,
    reviewer_provider: Provider,
    runner: AdapterRunner,
) -> IndependentReview | LabError:
    """Bind an independent AI review to host-calculated source identity."""

    if reviewer_provider not in ("claude", "codex"):
        return LabError(code=LabErrorCode.INVALID_INPUT)
    if reviewer_provider == candidate.generator_provider:
        return LabError(code=LabErrorCode.INDEPENDENCE_REQUIRED)
    try:
        _require_now(now)
        payload = {
            "source": candidate.source,
            "source_hash": candidate.source_hash,
            "evidence": evidence,
        }
        prompt = (
            "Review source safety and reproducibility. Do not invent performance or PnL.\n"
            + json.dumps(
                payload,
                allow_nan=False,
                default=_json_default,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError):
        return LabError(code=LabErrorCode.INVALID_INPUT)
    try:
        content = _ReviewContent.model_validate(
            runner(reviewer_provider, prompt, _REVIEW_SCHEMA)
        )
        return IndependentReview(
            reviewer_provider=reviewer_provider,
            generator_provider=candidate.generator_provider,
            approved=content.approved,
            source_hash=candidate.source_hash,
            candidate_version=candidate.version,
            observed_at=now,
            summary=content.summary,
        )
    except (ValidationError, TypeError, ValueError):
        return LabError(code=LabErrorCode.MALFORMED_OUTPUT)
    except Exception:
        return LabError(code=LabErrorCode.RUNNER_FAILED)


_PINNED_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")


class SandboxConfig(StrictModel):
    enabled: bool = False
    pinned_image: str
    timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
    cpus: float = Field(default=0.5, gt=0.0, le=2.0)
    memory_mb: int = Field(default=128, ge=32, le=512)
    pids_limit: int = Field(default=32, ge=8, le=64)
    max_output_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)

    @field_validator("pinned_image")
    @classmethod
    def _image_pinned(cls, value: str) -> str:
        if not _PINNED_IMAGE.fullmatch(value):
            raise ValueError("image must be explicitly digest-pinned")
        return value


class SandboxReadiness(StrictModel):
    pinned_image: str
    image_id: str
    verified_at: datetime

    @field_validator("image_id")
    @classmethod
    def _image_id(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("invalid image id")
        return value

    @field_validator("verified_at")
    @classmethod
    def _ready_time(cls, value: datetime) -> datetime:
        if not _aware(value):
            raise ValueError("verified_at must be timezone-aware")
        return value


def _minimal_docker_env() -> dict[str, str]:
    allowed = ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP")
    return {key: os.environ[key] for key in allowed if key in os.environ}


def verify_sandbox_readiness(
    config: SandboxConfig,
    now: datetime,
    subprocess_runner: SubprocessRunner = subprocess.run,
) -> SandboxReadiness | LabError:
    """Verify Docker and the already-present pinned image without pulling it."""

    if not config.enabled:
        return LabError(code=LabErrorCode.SANDBOX_DISABLED)
    try:
        _require_now(now)
        server = subprocess_runner(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            check=False,
            shell=False,
            env=_minimal_docker_env(),
        )
        if server.returncode != 0 or not str(server.stdout).strip():
            return LabError(code=LabErrorCode.SANDBOX_NOT_READY)
        image = subprocess_runner(
            ["docker", "image", "inspect", "--format", "{{.Id}}", config.pinned_image],
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            check=False,
            shell=False,
            env=_minimal_docker_env(),
        )
        image_id = str(image.stdout).strip()
        if image.returncode != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            return LabError(code=LabErrorCode.SANDBOX_NOT_READY)
        return SandboxReadiness(
            pinned_image=config.pinned_image, image_id=image_id, verified_at=now
        )
    except (subprocess.TimeoutExpired, TimeoutError):
        return LabError(code=LabErrorCode.SANDBOX_TIMEOUT)
    except Exception:
        return LabError(code=LabErrorCode.SANDBOX_NOT_READY)


class SignalSuggestion(StrictModel):
    symbol: str
    strategy_id: str
    signal: Literal["long", "flat", "exit"]
    confidence: float
    reason: str

    @field_validator("symbol", "strategy_id", "reason")
    @classmethod
    def _signal_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("signal fields must be non-empty")
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, value: float) -> float:
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be finite and in [0, 1]")
        return value


class SignalRunResult(StrictModel):
    source_hash: str
    candidate_version: str
    suggestions: tuple[SignalSuggestion, ...]


def _sandbox_harness(max_output_bytes: int) -> str:
    return (
        "import contextlib,json,os,runpy,sys;"
        "d=open(os.devnull,'w');o=contextlib.redirect_stdout(d);r=contextlib.redirect_stderr(d);"
        "o.__enter__();r.__enter__();"
        "m=runpy.run_path('/qp/candidate.py',run_name='candidate');"
        "f=m.get('generate_signals');assert callable(f);"
        "e=json.load(open('/qp/input.json',encoding='utf-8'));v=f(e);"
        "r.__exit__(None,None,None);o.__exit__(None,None,None);"
        "s=json.dumps({'suggestions':v},allow_nan=False,separators=(',',':'));"
        f"assert len(s.encode('utf-8'))<={max_output_bytes};print(s)"
    )


def build_docker_command(
    config: SandboxConfig, code_path: Path, input_path: Path
) -> list[str]:
    """Build the non-shell Docker argv; only two read-only temporary files mount."""

    if not code_path.is_absolute() or not input_path.is_absolute():
        raise ValueError("sandbox mount paths must be absolute")
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        f"--cpus={config.cpus}",
        f"--memory={config.memory_mb}m",
        f"--pids-limit={config.pids_limit}",
        "--ulimit=nofile=64:64",
        "--mount",
        f"type=bind,source={code_path},target=/qp/candidate.py,readonly",
        "--mount",
        f"type=bind,source={input_path},target=/qp/input.json,readonly",
        config.pinned_image,
        "python",
        "-I",
        "-c",
        _sandbox_harness(config.max_output_bytes),
    ]


class _RawSignalOutput(StrictModel):
    suggestions: list[SignalSuggestion]


def run_signal_code(
    candidate: Candidate,
    evidence: dict[str, Any],
    config: SandboxConfig,
    readiness: SandboxReadiness | None,
    subprocess_runner: SubprocessRunner | None = None,
) -> SignalRunResult | LabError:
    """Execute candidate signal code only inside a verified fail-closed sandbox."""

    if not config.enabled:
        return LabError(code=LabErrorCode.SANDBOX_DISABLED)
    if readiness is None or readiness.pinned_image != config.pinned_image:
        return LabError(code=LabErrorCode.SANDBOX_NOT_READY)
    from quantpilot.paper.process import bounded_run

    execute = subprocess_runner or bounded_run
    try:
        payload = json.dumps(
            evidence,
            allow_nan=False,
            default=_json_default,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return LabError(code=LabErrorCode.INVALID_INPUT)
    try:
        with tempfile.TemporaryDirectory(prefix="quantpilot-sandbox-") as directory:
            root = Path(directory).resolve()
            code_path = root / "candidate.py"
            input_path = root / "input.json"
            code_path.write_text(candidate.source, encoding="utf-8")
            input_path.write_text(payload, encoding="utf-8")
            code_path.chmod(0o444)
            input_path.chmod(0o444)
            command = build_docker_command(config, code_path, input_path)
            container_name = "qp-signal-" + uuid4().hex
            command[2:2] = ["--name", container_name]
            try:
                completed = execute(
                    command,
                    capture_output=True,
                    text=False,
                    timeout=config.timeout_seconds,
                    check=False,
                    shell=False,
                    env=_minimal_docker_env(),
                    **(
                        {"max_output_bytes": config.max_output_bytes}
                        if subprocess_runner is None
                        else {}
                    ),
                )
            finally:
                # Killing a timed-out Docker client does not kill its container.
                # This unique host-generated name can never target an unrelated service.
                try:
                    execute(
                        ["docker", "rm", "--force", container_name],
                        capture_output=True,
                        text=False,
                        timeout=5,
                        check=False,
                        shell=False,
                        env=_minimal_docker_env(),
                    )
                except Exception:
                    pass
        if completed.returncode != 0:
            return LabError(code=LabErrorCode.RUNNER_FAILED)
        stdout = (
            completed.stdout
            if isinstance(completed.stdout, bytes)
            else str(completed.stdout).encode("utf-8")
        )
        if len(stdout) > config.max_output_bytes:
            return LabError(code=LabErrorCode.SANDBOX_OUTPUT_LIMIT)
        raw = json.loads(stdout.decode("utf-8"))
        parsed = _RawSignalOutput.model_validate(raw)
        return SignalRunResult(
            source_hash=candidate.source_hash,
            candidate_version=candidate.version,
            suggestions=tuple(parsed.suggestions),
        )
    except (subprocess.TimeoutExpired, TimeoutError):
        return LabError(code=LabErrorCode.SANDBOX_TIMEOUT)
    except Exception:
        return LabError(code=LabErrorCode.MALFORMED_OUTPUT)


__all__ = [
    "Candidate",
    "CandidateSource",
    "FIRST_PAPER_TRIAL_CAP",
    "ForwardTradeEvidence",
    "IndependentReview",
    "LabError",
    "LabErrorCode",
    "LabState",
    "PromotionDecision",
    "SandboxConfig",
    "SandboxReadiness",
    "SignalRunResult",
    "SignalSuggestion",
    "VerificationEvidence",
    "build_docker_command",
    "evaluate_promotion",
    "move_to_paper_trial",
    "move_to_shadow",
    "register_generated",
    "replace_source",
    "run_candidate_generation",
    "run_candidate_review",
    "run_signal_code",
    "source_sha256",
    "verify_sandbox_readiness",
]
