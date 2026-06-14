from __future__ import annotations

from os import getenv

from quantpilot.packages.core.schemas import UserPolicy


def env_flag_enabled(name: str, *, default: bool = False) -> bool:
    raw = getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


def guarded_autopilot_flag_enabled(policy: UserPolicy) -> bool:
    return policy.guarded_autopilot_enabled or env_flag_enabled("GUARDED_AUTOPILOT_ENABLED")


def fully_automated_operator_flag_enabled(policy: UserPolicy | None = None) -> bool:
    policy_enabled = policy.fully_automated_operator_enabled if policy is not None else False
    return policy_enabled or env_flag_enabled("FULLY_AUTOMATED_OPERATOR_ENABLED")


def operator_kill_switch_engaged() -> bool:
    return env_flag_enabled("OPERATOR_KILL_SWITCH")


def live_trading_flag_enabled() -> bool:
    return env_flag_enabled("LIVE_TRADING_ENABLED")


def market_orders_enabled() -> bool:
    return env_flag_enabled("MARKET_ORDERS_ENABLED")
