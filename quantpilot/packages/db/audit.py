from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from quantpilot.packages.core.schemas import AuditLogEvent
from quantpilot.packages.db.repositories import InMemoryRepository


def _safe_state(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {"value": str(value)}


class AuditRecorder:
    def __init__(self, repository: InMemoryRepository[AuditLogEvent]) -> None:
        self._repository = repository

    def emit(
        self,
        *,
        user_id: str,
        entity_type: str,
        entity_id: str,
        action: str,
        before_state: Any = None,
        after_state: Any = None,
        source: str,
    ) -> AuditLogEvent:
        event = AuditLogEvent(
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_state=_safe_state(before_state),
            after_state=_safe_state(after_state),
            source=source,
        )
        return self._repository.add(event)
