"""Bounded, advisory-only AI adapters for the paper-trading lead.

Public integration API
----------------------
``run_assessment(evidence, now, primary="claude", runner=None)`` returns an
``Assessment`` or ``IntelligenceError``.  Only ``candidate_scores`` and
``strategy_scores`` are intended as numeric inputs to lead-owned runtime code.
``run_review(...)`` returns prose-only ``Review`` or ``IntelligenceError``.

An injected runner has the signature ``runner(provider, prompt, schema) ->
dict``.  When it is omitted, :func:`default_cli_runner` invokes the installed
subscription CLI with a bounded timeout and no tool or MCP authority.  The
models in this module cannot represent orders, approval, sizing, or execution.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Provider = Literal["claude", "codex"]
Runner = Callable[[str, str, dict[str, Any]], dict[str, Any]]
MAX_VALIDITY = timedelta(minutes=75)
CLI_TIMEOUT_SECONDS = 90


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _require_now(now: datetime) -> None:
    if not _aware(now):
        raise ValueError("now must be timezone-aware")


class IntelligenceErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    RUNNER_UNAVAILABLE = "runner_unavailable"
    RUNNER_TIMEOUT = "runner_timeout"
    RUNNER_FAILED = "runner_failed"
    MALFORMED_OUTPUT = "malformed_output"
    OUT_OF_ALLOWLIST = "out_of_allowlist"
    UNUSABLE_TIMESTAMP = "unusable_timestamp"
    PROSE_NUMERIC_CLAIM = "prose_numeric_claim"


class IntelligenceError(BaseModel):
    """Bounded failure value; it intentionally carries no exception/stderr text."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: IntelligenceErrorCode
    attempted_providers: tuple[Provider, ...] = ()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _AssessmentContent(_StrictModel):
    model: str
    candidate_scores: dict[str, float]
    strategy_scores: dict[str, float]
    reasons: dict[str, str]


class _ReviewContent(_StrictModel):
    model: str
    summary: str
    observations: list[str]
    risks: list[str]


class Assessment(_StrictModel):
    """Advisory scores stamped by trusted host code, never trading authority."""

    provider: Provider
    model: str
    observed_at: datetime
    expires_at: datetime
    candidate_scores: dict[str, float] = Field(default_factory=dict)
    strategy_scores: dict[str, float] = Field(default_factory=dict)
    reasons: dict[str, str] = Field(default_factory=dict)

    @field_validator("model")
    @classmethod
    def _model_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must be non-empty")
        return value

    @field_validator("observed_at", "expires_at")
    @classmethod
    def _aware_timestamp(cls, value: datetime) -> datetime:
        if not _aware(value):
            raise ValueError("assessment timestamps must be timezone-aware")
        return value

    @field_validator("candidate_scores", "strategy_scores")
    @classmethod
    def _unit_scores(cls, value: dict[str, float]) -> dict[str, float]:
        for key, score in value.items():
            if not key.strip():
                raise ValueError("score keys must be non-empty")
            if (
                isinstance(score, bool)
                or not math.isfinite(score)
                or not 0.0 <= score <= 1.0
            ):
                raise ValueError("scores must be finite and in [0, 1]")
        return value

    @field_validator("reasons")
    @classmethod
    def _reasons_are_text(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not reason.strip() for key, reason in value.items()):
            raise ValueError("reason keys and values must be non-empty")
        return value

    @model_validator(mode="after")
    def _bounded_interval(self) -> "Assessment":
        if self.expires_at <= self.observed_at:
            raise ValueError("expires_at must follow observed_at")
        if self.expires_at - self.observed_at > MAX_VALIDITY:
            raise ValueError("assessment validity exceeds 75 minutes")
        return self

    def usable(self, now: datetime) -> bool:
        """Return true only when observation is not future and expiry is exclusive."""

        _require_now(now)
        return self.observed_at <= now < self.expires_at


class Review(_StrictModel):
    """Prose-only observations; numeric report facts remain lead-owned."""

    provider: Provider
    model: str
    observed_at: datetime
    expires_at: datetime
    summary: str
    observations: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    @field_validator("model", "summary")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must be non-empty")
        return value

    @field_validator("observed_at", "expires_at")
    @classmethod
    def _review_aware(cls, value: datetime) -> datetime:
        if not _aware(value):
            raise ValueError("review timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _review_interval(self) -> "Review":
        if (
            self.expires_at <= self.observed_at
            or self.expires_at - self.observed_at > MAX_VALIDITY
        ):
            raise ValueError("invalid review validity")
        if any(not item.strip() for item in (*self.observations, *self.risks)):
            raise ValueError("review entries must be non-empty")
        return self

    def usable(self, now: datetime) -> bool:
        _require_now(now)
        return self.observed_at <= now < self.expires_at


_ASSESSMENT_CONTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "model": {"type": "string", "minLength": 1},
        "candidate_scores": {
            "type": "object",
            "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "strategy_scores": {
            "type": "object",
            "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "reasons": {
            "type": "object",
            "additionalProperties": {"type": "string", "minLength": 1},
        },
    },
    "required": ["model", "candidate_scores", "strategy_scores", "reasons"],
}

_REVIEW_CONTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "model": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "observations": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "risks": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "required": ["model", "summary", "observations", "risks"],
}


def _assessment_schema(symbols: set[str], strategies: set[str]) -> dict[str, Any]:
    """Close dynamic maps over trusted allowlists for strict CLI JSON schemas."""
    schema = deepcopy(_ASSESSMENT_CONTENT_SCHEMA)
    for name, keys in (
        ("candidate_scores", symbols),
        ("strategy_scores", strategies),
        ("reasons", symbols | strategies),
    ):
        value_schema = schema["properties"][name]["additionalProperties"]
        schema["properties"][name] = {
            "type": "object",
            "properties": {key: deepcopy(value_schema) for key in sorted(keys)},
            "required": sorted(keys),
            "additionalProperties": False,
        }
    return schema


def _alternate(provider: Provider) -> Provider:
    return "codex" if provider == "claude" else "claude"


def _allowlist(
    evidence: Mapping[str, Any], singular: str, plural: str
) -> set[str] | None:
    raw = evidence.get(f"{singular}_allowlist", evidence.get(plural))
    if not isinstance(raw, (list, tuple, set)):
        return None
    values = {item for item in raw if isinstance(item, str) and item.strip()}
    return values if len(values) == len(raw) else None


def _validated_evidence(evidence: dict[str, Any]) -> tuple[set[str], set[str]] | None:
    if not isinstance(evidence, dict):
        return None
    symbols = _allowlist(evidence, "symbol", "symbols")
    strategies = _allowlist(evidence, "strategy", "strategies")
    if symbols is None or strategies is None:
        return None
    try:
        json.dumps(
            evidence,
            allow_nan=False,
            default=_json_default,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return None
    return symbols, strategies


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        if not _aware(value):
            raise TypeError("naive datetime")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError("unsupported evidence value")


def _prompt(kind: str, evidence: dict[str, Any], now: datetime) -> str:
    payload = json.dumps(
        evidence,
        allow_nan=False,
        default=_json_default,
        separators=(",", ":"),
        sort_keys=True,
    )
    if kind == "assessment":
        instruction = (
            "Return advisory scores only. Candidate score keys and strategy score keys must come "
            "from their supplied allowlists. Do not propose orders, quantities, prices, approval, "
            "execution, tools, or actions. Host code supplies provider and timestamps."
        )
    else:
        instruction = (
            "Return qualitative prose only. Do not calculate, repeat, or infer PnL, returns, prices, "
            "amounts, counts, percentages, or any other numeric report data. Do not propose orders."
        )
    return f"{instruction}\nTrusted observation time: {now.isoformat()}\nEvidence JSON:\n{payload}"


def _sanitized_environment() -> dict[str, str]:
    allowed = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
    )
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _parse_cli_json(stdout: str, output_file: Path | None = None) -> dict[str, Any]:
    text = (
        output_file.read_text(encoding="utf-8")
        if output_file is not None and output_file.exists()
        else stdout
    )
    raw = json.loads(text)
    if isinstance(raw, dict) and isinstance(raw.get("structured_output"), dict):
        return raw["structured_output"]
    if isinstance(raw, dict) and isinstance(raw.get("result"), str):
        nested = json.loads(raw["result"])
        if isinstance(nested, dict):
            return nested
    if not isinstance(raw, dict):
        raise ValueError("CLI output is not an object")
    return raw


def default_cli_runner(
    provider: str, prompt: str, schema: dict[str, Any]
) -> dict[str, Any]:
    """Invoke one subscription CLI without exposing ambient credentials or tools.

    Only bounded error classes escape; stdout/stderr are never copied into errors.
    Codex runs in an empty read-only working directory with user config/rules
    ignored. Claude runs safe/restricted with an empty tool set and MCP config.
    """

    if provider not in ("claude", "codex"):
        raise ValueError("unsupported provider")
    with tempfile.TemporaryDirectory(
        prefix="quantpilot-intelligence-", ignore_cleanup_errors=True
    ) as directory:
        root = Path(directory)
        schema_path = root / "schema.json"
        schema_path.write_text(
            json.dumps(schema, separators=(",", ":")), encoding="utf-8"
        )
        output_path: Path | None = None
        if provider == "codex":
            output_path = root / "result.json"
            command = [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--disable",
                "multi_agent",
                "--disable",
                "multi_agent_v2",
                "--disable",
                "view_image",
                "--disable",
                "image_generation",
                "--disable",
                "code_mode",
                "--config",
                'web_search="disabled"',
                "--disable",
                "shell_tool",
                "--disable",
                "unified_exec",
                "--disable",
                "browser_use",
                "--disable",
                "browser_use_external",
                "--disable",
                "computer_use",
                "--disable",
                "apps",
                "--disable",
                "plugins",
                "--config",
                "mcp_servers={}",
                "--sandbox",
                "read-only",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
        else:
            command = [
                "claude",
                "--print",
                "--safe-mode",
                "--restricted",
                "--tools",
                "",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--permission-mode",
                "dontAsk",
                "--permission-prompts",
                "none",
                "--no-session-persistence",
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(schema, separators=(",", ":")),
                "-",
            ]
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=root,
            env=_sanitized_environment(),
            timeout=CLI_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("runner failed")
        parsed = _parse_cli_json(completed.stdout, output_path)
        if "model" in parsed:
            # Prefer CLI telemetry; a model's self-description is not provenance.
            model_id = None
            if provider == "claude":
                metadata = json.loads(completed.stdout)
                usage = (
                    metadata.get("modelUsage", {}) if isinstance(metadata, dict) else {}
                )
                if isinstance(usage, dict) and len(usage) == 1:
                    model_id = next(iter(usage))
            else:
                match = re.search(
                    r"(?m)^model:\s*([A-Za-z0-9_.:/-]+)\s*$", completed.stderr or ""
                )
                if match:
                    model_id = match.group(1)
            parsed["model"] = (
                model_id
                if isinstance(model_id, str)
                and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", model_id)
                else "cli_model_unreported"
            )
        return parsed


def _error_code(exc: BaseException) -> IntelligenceErrorCode:
    if isinstance(exc, (subprocess.TimeoutExpired, TimeoutError)):
        return IntelligenceErrorCode.RUNNER_TIMEOUT
    if isinstance(exc, FileNotFoundError):
        return IntelligenceErrorCode.RUNNER_UNAVAILABLE
    if isinstance(exc, (json.JSONDecodeError, ValueError, TypeError)):
        return IntelligenceErrorCode.MALFORMED_OUTPUT
    return IntelligenceErrorCode.RUNNER_FAILED


def run_assessment(
    evidence: dict[str, Any],
    now: datetime,
    primary: Provider = "claude",
    runner: Runner | None = None,
) -> Assessment | IntelligenceError:
    """Try exactly ``primary`` then its alternate and return a bounded value."""

    try:
        _require_now(now)
    except (TypeError, ValueError):
        return IntelligenceError(code=IntelligenceErrorCode.INVALID_INPUT)
    allowlists = _validated_evidence(evidence)
    if primary not in ("claude", "codex") or allowlists is None:
        return IntelligenceError(code=IntelligenceErrorCode.INVALID_INPUT)
    symbols, strategies = allowlists
    invoke = runner or default_cli_runner
    attempted: list[Provider] = []
    last_code = IntelligenceErrorCode.RUNNER_FAILED
    for provider in (primary, _alternate(primary)):
        attempted.append(provider)
        try:
            raw = invoke(
                provider,
                _prompt("assessment", evidence, now),
                _assessment_schema(symbols, strategies),
            )
            content = _AssessmentContent.model_validate(raw)
            data = content.model_dump()
            if set(data) != {"model", "candidate_scores", "strategy_scores", "reasons"}:
                raise ValueError("unexpected assessment fields")
            assessment = Assessment(
                provider=provider,
                observed_at=now,
                expires_at=now + MAX_VALIDITY,
                **data,
            )
            if not set(assessment.candidate_scores).issubset(symbols):
                last_code = IntelligenceErrorCode.OUT_OF_ALLOWLIST
                continue
            if not set(assessment.strategy_scores).issubset(strategies):
                last_code = IntelligenceErrorCode.OUT_OF_ALLOWLIST
                continue
            if not assessment.usable(now):
                last_code = IntelligenceErrorCode.UNUSABLE_TIMESTAMP
                continue
            return assessment
        except (
            Exception
        ) as exc:  # boundary converts all provider faults to bounded codes
            last_code = _error_code(exc)
    return IntelligenceError(code=last_code, attempted_providers=tuple(attempted))


_NUMERIC_OR_PNL = re.compile(
    r"\d|\bp\s*&?\s*l\b|\bpnl\b|\bprofit\b|\breturn\b", re.IGNORECASE
)


def run_review(
    evidence: dict[str, Any],
    now: datetime,
    primary: Provider = "claude",
    runner: Runner | None = None,
) -> Review | IntelligenceError:
    """Return validated qualitative prose; all numeric reporting stays with lead code."""

    try:
        _require_now(now)
    except (TypeError, ValueError):
        return IntelligenceError(code=IntelligenceErrorCode.INVALID_INPUT)
    if primary not in ("claude", "codex") or _validated_evidence(evidence) is None:
        return IntelligenceError(code=IntelligenceErrorCode.INVALID_INPUT)
    invoke = runner or default_cli_runner
    attempted: list[Provider] = []
    last_code = IntelligenceErrorCode.RUNNER_FAILED
    for provider in (primary, _alternate(primary)):
        attempted.append(provider)
        try:
            raw = invoke(
                provider, _prompt("review", evidence, now), _REVIEW_CONTENT_SCHEMA
            )
            content = _ReviewContent.model_validate(raw)
            data = content.model_dump()
            if set(data) != {"model", "summary", "observations", "risks"}:
                raise ValueError("unexpected review fields")
            prose = (data["summary"], *data["observations"], *data["risks"])
            if any(_NUMERIC_OR_PNL.search(item) for item in prose):
                last_code = IntelligenceErrorCode.PROSE_NUMERIC_CLAIM
                continue
            review = Review(
                provider=provider,
                observed_at=now,
                expires_at=now + MAX_VALIDITY,
                model=data["model"],
                summary=data["summary"],
                observations=tuple(data["observations"]),
                risks=tuple(data["risks"]),
            )
            if review.usable(now):
                return review
            last_code = IntelligenceErrorCode.UNUSABLE_TIMESTAMP
        except Exception as exc:
            last_code = _error_code(exc)
    return IntelligenceError(code=last_code, attempted_providers=tuple(attempted))


__all__ = [
    "Assessment",
    "CLI_TIMEOUT_SECONDS",
    "IntelligenceError",
    "IntelligenceErrorCode",
    "MAX_VALIDITY",
    "Provider",
    "Review",
    "Runner",
    "default_cli_runner",
    "run_assessment",
    "run_review",
]
