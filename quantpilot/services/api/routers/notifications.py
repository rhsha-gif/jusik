from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import OperatorNotification
from quantpilot.packages.db.repositories import RepositoryError
from quantpilot.services.api.dependencies import get_harness_service


router = APIRouter()


@router.get("/notifications")
def list_notifications(
    unacknowledged_only: bool = False,
    service: HarnessService = Depends(get_harness_service),
) -> list[OperatorNotification]:
    return service.list_notifications(unacknowledged_only=unacknowledged_only)


@router.post("/notifications/{notification_id}/acknowledge")
def acknowledge_notification(
    notification_id: str,
    service: HarnessService = Depends(get_harness_service),
) -> OperatorNotification:
    try:
        return service.acknowledge_notification(notification_id)
    except (RepositoryError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
