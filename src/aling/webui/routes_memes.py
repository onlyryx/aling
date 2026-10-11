"""Authenticated, bounded raw-image uploads and catalog-only removal."""

from collections import deque
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from aling.services.memes import MemeCatalogError, MemeQuotaError, MemeReadOnlyError

router = APIRouter(prefix="/api/admin/memes")
ResourceID = Annotated[str, Path(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]


class UploadAdmission:
    def __init__(self, *, clock=monotonic):
        self.clock = clock
        self.active = 0
        self.attempts = {}

    def acquire(self, owner):
        now = self.clock()
        recent = self.attempts.setdefault(owner, deque())
        while recent and recent[0] <= now - 600:
            recent.popleft()
        if len(recent) >= 30:
            raise HTTPException(429, "meme_upload_rate_limited")
        recent.append(now)
        if self.active >= 2:
            raise HTTPException(429, "meme_upload_busy")
        self.active += 1

    def release(self):
        self.active -= 1


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    description: str = Field(max_length=200)
    tags: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(max_length=32)
    source: str = Field(default="", max_length=500)
    license: str = Field(default="", max_length=500)


def _provider(request):
    return request.app.state.panel_runtime.memes


def _error(exc):
    if isinstance(exc, MemeReadOnlyError):
        return HTTPException(409, "meme_read_only")
    if isinstance(exc, MemeQuotaError):
        return HTTPException(409, "meme_quota_exceeded")
    if isinstance(exc, MemeCatalogError):
        return HTTPException(422, "meme_invalid_asset")
    return HTTPException(503, "meme_storage_unavailable")


@router.get("")
async def list_memes(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    query: str = Query(default="", max_length=100),
):
    try:
        return _provider(request).list_assets(offset=offset, limit=limit, query=query)
    except (MemeCatalogError, OSError) as exc:
        raise _error(exc) from None


@router.post("/upload", status_code=201)
async def upload(request: Request):
    admission = request.app.state.panel_uploads
    admission.acquire(request.state.panel_session.owner_id)
    try:
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > 5_000_000:
                raise HTTPException(413, "meme_upload_too_large")
            content.extend(chunk)
        resource = await _provider(request).add_image(bytes(content))
        return {"id": resource}
    except (MemeCatalogError, OSError) as exc:
        raise _error(exc) from None
    finally:
        admission.release()


@router.get("/{resource_id}/image")
async def image(request: Request, resource_id: ResourceID):
    try:
        validated = _provider(request).read_image(resource_id)
        return Response(
            validated.content,
            media_type=validated.mime_type,
            headers={
                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; sandbox",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )
    except (MemeCatalogError, OSError) as exc:
        raise _error(exc) from None


@router.patch("/{resource_id}")
async def metadata(request: Request, resource_id: ResourceID, body: Metadata):
    try:
        await _provider(request).update_metadata(resource_id, **body.model_dump())
        return {"updated": True}
    except (MemeCatalogError, OSError) as exc:
        raise _error(exc) from None


@router.delete("/{resource_id}")
async def remove(request: Request, resource_id: ResourceID):
    try:
        return {"removed": await _provider(request).remove_entry(resource_id)}
    except (MemeCatalogError, OSError) as exc:
        raise _error(exc) from None
