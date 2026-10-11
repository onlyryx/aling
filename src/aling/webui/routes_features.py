"""Feature changes delegate to the same consent and permission service as QQ."""

import sqlite3
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from aling.core.permissions import PermissionContext
from aling.core.storage import FEATURES
from aling.services.features import FeatureDisabledError, MemoryConfirmationRequired

router = APIRouter(prefix="/api/admin")
GroupID = Annotated[int, Path(gt=0, le=2**63 - 1)]


class Toggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=1, max_length=100)


def _feature(feature: str) -> str:
    if feature not in FEATURES:
        raise HTTPException(404, "unknown_feature")
    return feature


def _context(request: Request, group_id: int) -> PermissionContext:
    return PermissionContext(request.state.panel_session.owner_id, group_id, "member")


async def _run(request: Request, operation):
    try:
        return await operation
    except PermissionError:
        raise HTTPException(403, "scope_restricted") from None
    except MemoryConfirmationRequired:
        raise HTTPException(409, "memory_confirmation_required") from None
    except FeatureDisabledError:
        raise HTTPException(409, "feature_unavailable") from None
    except (sqlite3.Error, OSError):
        request.app.state.panel_runtime.status.record_diagnostic("storage", "save_failed")
        raise HTTPException(503, "storage_unavailable") from None


async def _states(request: Request, group_id: int | None):
    service = request.app.state.panel_runtime.features
    return {
        feature: asdict(await _run(request, service.state(group_id, feature)))
        for feature in sorted(FEATURES)
    }


@router.get("/features/global")
async def global_features(request: Request):
    return await _states(request, None)


@router.get("/features/global/{feature}")
async def global_feature(request: Request, feature: str):
    return asdict(
        await _run(request, request.app.state.panel_runtime.features.state(None, _feature(feature)))
    )


@router.put("/features/global/{feature}")
async def set_global_feature(request: Request, feature: str, body: Toggle):
    service = request.app.state.panel_runtime.features
    await _run(
        request,
        service.set_global(request.state.panel_session.owner_id, _feature(feature), body.enabled),
    )
    return await global_feature(request, feature)


@router.get("/groups/{group_id}/features")
async def group_features(request: Request, group_id: GroupID):
    return await _states(request, group_id)


@router.put("/groups/{group_id}/features/{feature}")
async def set_group_feature(request: Request, group_id: GroupID, feature: str, body: Toggle):
    service = request.app.state.panel_runtime.features
    await _run(request, service.set(_context(request, group_id), _feature(feature), body.enabled))
    return asdict(await _run(request, service.state(group_id, feature)))


@router.post("/groups/{group_id}/memory-confirmation")
async def request_memory(request: Request, group_id: GroupID):
    service = request.app.state.panel_runtime.features
    result = await _run(
        request,
        service.request_memory(
            _context(request, group_id), scope=f"webui:{request.state.panel_session.session_id}"
        ),
    )
    return asdict(result)


@router.post("/groups/{group_id}/memory-confirmation/confirm")
async def confirm_memory(request: Request, group_id: GroupID, body: Confirmation):
    service = request.app.state.panel_runtime.features
    await _run(
        request,
        service.confirm_memory(
            _context(request, group_id),
            scope=f"webui:{request.state.panel_session.session_id}",
            ticket=body.ticket,
        ),
    )
    return asdict(await _run(request, service.state(group_id, "memory")))
