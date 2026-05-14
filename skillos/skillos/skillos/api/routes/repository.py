"""Repository maintenance routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from ..deps import AppState, get_app_state

router = APIRouter(prefix="/repository", tags=["repository"])


@router.get("/status", response_model=Dict[str, Any])
async def get_repository_status(app: AppState = Depends(get_app_state)) -> Dict[str, Any]:
    if hasattr(app.wiki, "repo_status"):
        return await app.wiki.repo_status()
    return {"backend": "memory", "is_git_repo": False, "dirty": False}


@router.get("/events", response_model=List[Dict[str, Any]])
async def get_repository_events(
    limit: int = Query(100, ge=1, le=1000),
    app: AppState = Depends(get_app_state),
) -> List[Dict[str, Any]]:
    if hasattr(app.wiki, "read_events"):
        return await app.wiki.read_events(limit=limit)
    return []


@router.post("/rebuild-index", response_model=Dict[str, Any])
async def rebuild_repository_index(app: AppState = Depends(get_app_state)) -> Dict[str, Any]:
    if hasattr(app.wiki, "rebuild_index"):
        return await app.wiki.rebuild_index()
    return {"backend": "memory", "rebuilt": False}
