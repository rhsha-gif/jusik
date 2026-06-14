from __future__ import annotations

import os

from quantpilot.packages.core.schemas import DataMode

_UNSAFE_MODES: frozenset[DataMode] = frozenset(
    {DataMode.live_canary, DataMode.live_scaled, DataMode.live_trading}
)


class DataModeConfigError(ValueError):
    """Raised when DATA_MODE is explicitly set to an unsupported value."""


def raw_data_mode() -> str:
    """Return the normalized DATA_MODE value, defaulting only when the env var is unset."""
    return os.environ.get("DATA_MODE", "fixture").strip().lower()


def resolve_data_mode(raw: str | None = None) -> DataMode:
    """Return the active DataMode from DATA_MODE, failing closed on unsupported values."""
    value = raw_data_mode() if raw is None else raw.strip().lower()
    try:
        return DataMode(value)
    except ValueError:
        supported = ", ".join(mode.value for mode in DataMode)
        raise DataModeConfigError(f"Unsupported DATA_MODE {value!r}; supported values: {supported}")


def is_data_mode_safe(mode: DataMode) -> bool:
    """Return False for modes that imply real live order routing."""
    return mode not in _UNSAFE_MODES
