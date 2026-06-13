from __future__ import annotations

from pathlib import Path


def test_level5_safety_defaults_are_documented_disabled() -> None:
    env_example = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")

    assert "LIVE_TRADING_ENABLED=false" in env_example
    assert "GUARDED_AUTOPILOT_ENABLED=false" in env_example
    assert "FULLY_AUTOMATED_OPERATOR_ENABLED=false" in env_example
    assert "MARKET_ORDERS_ENABLED=false" in env_example
    assert "BROKER_MODE=mock" in env_example
    assert "DATA_MODE=fixture" in env_example
