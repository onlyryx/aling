"""Safe operational summaries; GET never calls OneBot APIs."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from aling import __version__

router = APIRouter(prefix="/api/admin")


@router.get("/overview")
async def overview(request: Request):
    runtime = request.app.state.panel_runtime
    return {
        **runtime.status.overview(version=__version__),
        "model": {"configured": runtime.settings.ai_configured, "name": runtime.settings.ai_model},
        "persona": {"configured": bool(runtime.settings.ai_system_prompt.strip())},
    }


@router.post("/overview/refresh-qq")
async def refresh_qq(request: Request):
    runtime = request.app.state.panel_runtime
    return await runtime.status.refresh_qq(runtime.status.bot)


@router.get("/groups")
async def groups(
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    runtime = request.app.state.panel_runtime
    try:
        saved = await runtime.store.list_configured_group_ids()
    except (sqlite3.Error, OSError):
        runtime.status.record_diagnostic("storage", "read_failed")
        raise HTTPException(503, "storage_unavailable") from None
    ids = sorted(set(saved) | runtime.settings.allowed_groups | set(runtime.status.group_snapshot))
    return {
        "items": [
            {
                "group_id": group_id,
                "name": runtime.status.group_snapshot.get(group_id),
                "allowed": runtime.settings.group_allowed(group_id),
            }
            for group_id in ids[offset : offset + limit]
        ],
        "total": len(ids),
        "offset": offset,
        "limit": limit,
        "queried_at": runtime.status.groups_queried_at,
        "truncated": runtime.status.groups_truncated,
    }


@router.post("/groups/refresh")
async def refresh_groups(request: Request):
    runtime = request.app.state.panel_runtime
    if not await runtime.status.refresh_groups(runtime.status.bot):
        raise HTTPException(409, "qq_unavailable")
    return await groups(request)
