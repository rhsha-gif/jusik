"""Run required offline checks without inheriting broker/provider credentials."""

from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    # Filter names before obtaining any values. Never print the resulting environment.
    env = {
        k: os.environ[k]
        for k in os.environ
        if not k.startswith(
            ("KIS_", "SLACK_", "QUANTPILOT_SLACK_", "OPENAI_", "ANTHROPIC_")
        )
        and not any(
            word in k.upper() for word in ("TOKEN", "SECRET", "API_KEY", "PASSWORD")
        )
        and k
        not in {"QUANTPILOT_RUNTIME_ROLE", "BROKER_MODE", "DATA_MODE", "LOCAL_DATA_DIR"}
    }
    for name in (
        "LIVE_TRADING_ENABLED",
        "MARKET_ORDERS_ENABLED",
        "FULLY_AUTOMATED_OPERATOR_ENABLED",
        "GUARDED_AUTOPILOT_ENABLED",
    ):
        env[name] = "false"
    env["BROKER_MODE"] = "mock"
    env["DATA_MODE"] = "fixture"
    temp_root = Path.home() / ".codex" / "paper-verification"
    temp_root.mkdir(parents=True, exist_ok=True)
    base = tempfile.mkdtemp(prefix="pytest-", dir=temp_root)
    targets = sys.argv[1:] or ["quantpilot/tests"]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *targets,
            "-p",
            "no:cacheprovider",
            "--basetemp",
            base,
        ],
        cwd=root,
        env=env,
    )
    if result.returncode:
        raise SystemExit(result.returncode)
    if not sys.argv[1:]:
        raise SystemExit(
            subprocess.run(
                [sys.executable, "-m", "quantpilot.jobs.run_smoke"], cwd=root, env=env
            ).returncode
        )
