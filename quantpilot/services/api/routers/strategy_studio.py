from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import StrategyDraft
from quantpilot.packages.db.repositories import RepositoryError
from quantpilot.services.api.dependencies import get_harness_service


router = APIRouter()


class StrategyDraftRequest(BaseModel):
    symbols: list[str] = []
    sectors: list[str] = []
    note: str = ""


@router.post("/strategy-studio/draft")
def create_strategy_draft(
    request: StrategyDraftRequest,
    service: HarnessService = Depends(get_harness_service),
) -> StrategyDraft:
    try:
        return service.create_strategy_draft(
            symbols=request.symbols, sectors=request.sectors, note=request.note
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/strategy-studio/drafts/{draft_id}")
def get_strategy_draft(
    draft_id: str,
    service: HarnessService = Depends(get_harness_service),
) -> StrategyDraft:
    try:
        return service.repositories.strategy_drafts.require(draft_id)
    except (RepositoryError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/strategy-studio/drafts/{draft_id}/validate")
def validate_strategy_draft(
    draft_id: str,
    service: HarnessService = Depends(get_harness_service),
) -> dict[str, object]:
    try:
        return service.validate_strategy_draft(draft_id)
    except (RepositoryError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
