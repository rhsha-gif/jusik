from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from fastapi import HTTPException

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.db.repositories import RepositoryRegistry


repositories = RepositoryRegistry()
harness_service = HarnessService(repositories)
T = TypeVar("T")


def get_harness_service() -> HarnessService:
    return harness_service


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
