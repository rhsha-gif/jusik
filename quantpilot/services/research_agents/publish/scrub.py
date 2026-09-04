"""Secret scrubbing for text that leaves the machine.

An LLM wrote the text; the environment it ran in holds credentials. Three
layers: the literal values of any credential-looking environment variable, a
few well-known token shapes, and the SecondBrain seal mark on a whole line.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

REDACTED = "[REDACTED]"
SEAL_MARK = "[봉인]"
_ENV_NAME_RE = re.compile(r"(KEY|SECRET|TOKEN|WEBHOOK|PASSWORD|PASSWD|CREDENTIAL)", re.IGNORECASE)
_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{16,}=*"),
    re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}\b"),
    re.compile(r"\bxox[abposre]-[A-Za-z0-9\-]{10,}\b"),  # Slack bot/user/app tokens (gate finding SG-001)
    re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/_\-]+"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
)
_MIN_VALUE_LEN = 8  # shorter env values ("false", "mock") are not secrets and would over-match


@dataclass(frozen=True)
class ScrubResult:
    text: str
    replaced: int


def _credential_values(environ: dict[str, str]) -> list[str]:
    values = [v for k, v in environ.items() if _ENV_NAME_RE.search(k) and len(v) >= _MIN_VALUE_LEN]
    return sorted(set(values), key=len, reverse=True)


def scrub(text: str, environ: dict[str, str] | None = None) -> ScrubResult:
    env = dict(os.environ) if environ is None else environ
    replaced = 0
    out = text
    for value in _credential_values(env):
        count = out.count(value)
        if count:
            out = out.replace(value, REDACTED)
            replaced += count
    for pattern in _PATTERNS:
        out, count = pattern.subn(REDACTED, out)
        replaced += count
    lines = []
    for line in out.splitlines():
        if SEAL_MARK in line:
            lines.append(REDACTED)
            replaced += 1
        else:
            lines.append(line)
    if out.endswith("\n"):
        lines.append("")
    return ScrubResult(text="\n".join(lines), replaced=replaced)
