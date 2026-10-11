"""Schedule management backed by the runtime scheduler and sender."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from aling.core.permissions import PermissionContext

router = APIRouter(prefix="/api/admin")
GroupID = Annotated[int, Path(gt=0, le=2**63 - 1)]


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hour: StrictInt = Field(ge=0, le=23)
    minute: StrictInt = Field(ge=0, le=59)
    text: StrictStr = Field(min_length=1, max_length=500)

    @field_validator("text")
    @classmethod
    def valid_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Schedule text must be nonblank")
        return value


class ProactiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


def _context(request: Request, group_id: int) -> PermissionContext:
    return PermissionContext(request.state.panel_session.owner_id, group_id, "member")


def _inactive_reasons(settings, status, group_id: int, proactive_enabled: bool) -> list[str]:
    reasons = []
    if not settings.proactive_enabled:
        reasons.append("global_off")
    if not proactive_enabled:
        reasons.append("group_off")
    if not settings.group_allowed(group_id):
        reasons.append("scope_restricted")
    if not status.connected:
        reasons.append("onebot_disconnected")
    return reasons


async def _item(request: Request, group_id: int, schedule=None, claim_dates=None) -> dict:
    runtime = request.app.state.panel_runtime
    proactive_enabled = await runtime.store.is_proactive_enabled(group_id)
    inactive_reasons = _inactive_reasons(
        runtime.settings, runtime.status, group_id, proactive_enabled
    )
    attempted_dates = [
        value
        for value in (
            (claim_dates or {}).get(group_id),
            schedule.last_attempted_date if schedule else None,
        )
        if value is not None
    ]
    return {
        "group_id": group_id,
        "schedule": (
            {
                "hour": schedule.hour,
                "minute": schedule.minute,
                "text": schedule.text,
                "last_attempted_date": max(attempted_dates, default=None),
            }
            if schedule
            else None
        ),
        "proactive_enabled": proactive_enabled,
        "active": not inactive_reasons,
        "inactive_reasons": inactive_reasons,
    }


async def _run(request: Request, operation):
    try:
        return await operation
    except PermissionError:
        raise HTTPException(403, "scope_restricted") from None
    except ValueError:
        raise HTTPException(422, "invalid_schedule") from None
    except (sqlite3.Error, OSError):
        request.app.state.panel_runtime.status.record_diagnostic("storage", "save_failed")
        raise HTTPException(503, "storage_unavailable") from None


async def _read(request: Request, operation):
    try:
        return await operation
    except (sqlite3.Error, OSError):
        request.app.state.panel_runtime.status.record_diagnostic("storage", "read_failed")
        raise HTTPException(503, "storage_unavailable") from None


@router.get("/schedules")
async def schedules(request: Request):
    runtime = request.app.state.panel_runtime
    try:
        configured = await runtime.store.list_configured_group_ids()
        saved = await runtime.store.list_schedules()
        claim_dates = await runtime.store.list_schedule_claim_dates()
        group_ids = sorted(
            set(configured) | runtime.settings.allowed_groups | set(runtime.status.group_snapshot)
        )
        schedules_by_group = {schedule.group_id: schedule for schedule in saved}
        items = [
            await _item(request, group_id, schedules_by_group.get(group_id), claim_dates)
            for group_id in group_ids
            if runtime.settings.group_allowed(group_id)
        ]
    except (sqlite3.Error, OSError):
        runtime.status.record_diagnostic("storage", "read_failed")
        raise HTTPException(503, "storage_unavailable") from None
    settings = runtime.settings
    return {
        "conditions": {
            "timezone": settings.proactive_timezone,
            "quiet_start": settings.proactive_quiet_start.isoformat(timespec="minutes"),
            "quiet_end": settings.proactive_quiet_end.isoformat(timespec="minutes"),
            "global_enabled": settings.proactive_enabled,
            "cooldown_seconds": settings.proactive_cooldown_seconds,
            "max_per_hour": settings.proactive_max_per_hour,
            "max_per_day": settings.proactive_max_per_day,
            "onebot_connected": runtime.status.connected,
        },
        "items": items,
    }


@router.put("/groups/{group_id}/schedule")
async def set_schedule(request: Request, group_id: GroupID, body: ScheduleInput):
    runtime = request.app.state.panel_runtime
    await _run(
        request,
        runtime.scheduler.set_schedule(
            _context(request, group_id), body.hour, body.minute, body.text
        ),
    )
    schedule = await _read(request, runtime.scheduler.get_schedule(group_id))
    claim_dates = await _read(request, runtime.store.list_schedule_claim_dates())
    return await _read(request, _item(request, group_id, schedule, claim_dates))


@router.delete("/groups/{group_id}/schedule")
async def delete_schedule(request: Request, group_id: GroupID):
    runtime = request.app.state.panel_runtime
    deleted = await _run(request, runtime.scheduler.delete_schedule(_context(request, group_id)))
    claim_dates = await _read(request, runtime.store.list_schedule_claim_dates())
    return {
        **await _read(request, _item(request, group_id, claim_dates=claim_dates)),
        "deleted": deleted,
    }


@router.put("/groups/{group_id}/proactive")
async def set_proactive(request: Request, group_id: GroupID, body: ProactiveInput):
    runtime = request.app.state.panel_runtime
    await _run(
        request,
        runtime.proactive.set_enabled(_context(request, group_id), body.enabled),
    )
    schedule = await _read(request, runtime.scheduler.get_schedule(group_id))
    claim_dates = await _read(request, runtime.store.list_schedule_claim_dates())
    return await _read(request, _item(request, group_id, schedule, claim_dates))
