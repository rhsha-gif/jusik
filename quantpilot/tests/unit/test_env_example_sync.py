from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
RUNTIME_ROOT = REPO_ROOT / "quantpilot"

# Every way runtime code reads process environment variables.
ENV_READ_PATTERN = re.compile(
    r"(?:os\.environ\.get\(|os\.getenv\(|os\.environ\[|_required_env\(|environment\.get\()"
    r"\s*[\"']([A-Z][A-Z0-9_]*)[\"']"
)
ENV_EXAMPLE_NAME_PATTERN = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)


def _runtime_python_files() -> list[Path]:
    files = []
    for path in RUNTIME_ROOT.rglob("*.py"):
        parts = path.relative_to(RUNTIME_ROOT).parts
        if parts[0] in {"tests", "apps"}:
            continue
        files.append(path)
    return files


def _env_vars_read_by_runtime_code() -> dict[str, list[str]]:
    reads: dict[str, list[str]] = {}
    for path in _runtime_python_files():
        source = path.read_text(encoding="utf-8")
        for name in ENV_READ_PATTERN.findall(source):
            reads.setdefault(name, []).append(str(path.relative_to(REPO_ROOT)))
    return reads


def test_every_runtime_env_var_is_documented_in_env_example() -> None:
    documented = set(ENV_EXAMPLE_NAME_PATTERN.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))
    reads = _env_vars_read_by_runtime_code()

    missing = {name: files for name, files in reads.items() if name not in documented}

    assert not missing, (
        ".env.example is out of sync; document these environment variables "
        f"(or update this guard if they are intentionally internal): {missing}"
    )


def test_guard_scans_known_env_read_sites() -> None:
    # Canary: if these known reads stop being detected, the scanner regex or
    # file walk broke and the sync guard above is silently vacuous.
    reads = _env_vars_read_by_runtime_code()

    assert "LIVE_TRADING_ENABLED" in reads
    assert "KIS_APP_KEY" in reads
    assert "KIS_PAPER_STATE_DB" in reads
