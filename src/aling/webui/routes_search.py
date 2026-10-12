"""Owner controls and safe diagnostics for controlled search."""

import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from aling.core.storage import (
    DeploymentSearchPolicy,
    GroupSearchPolicy,
    SearchLimits,
    SearchSourceRule,
)
from aling.services.search_budget import TaskBudget

router = APIRouter(prefix="/api/admin")
GroupID = Annotated[int, Path(gt=0, le=2**63 - 1)]
Purpose = Literal["technical", "ordinary_meme", "gaming", "hobby", "practical"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LimitsInput(StrictModel):
    task_seconds: StrictInt = Field(default=60, ge=10, le=60)
    work_seconds: StrictInt = Field(default=57, ge=7, le=57)
    max_tool_rounds: StrictInt = Field(default=3, ge=1, le=3)
    max_model_requests: StrictInt = Field(default=5, ge=1, le=5)
    max_external_operations: StrictInt = Field(default=4, ge=1, le=4)
    max_searches: StrictInt = Field(default=1, ge=1, le=1)
    max_results: StrictInt = Field(default=3, ge=1, le=3)
    request_timeout_seconds: StrictInt = Field(default=10, ge=1, le=10)
    instance_concurrency: StrictInt = Field(default=2, ge=1, le=2)
    group_concurrency: StrictInt = Field(default=1, ge=1, le=1)
    user_cooldown_seconds: StrictInt = Field(default=15, ge=15, le=3600)
    user_hourly_limit: StrictInt = Field(default=10, ge=1, le=10)
    group_hourly_limit: StrictInt = Field(default=30, ge=1, le=30)
    instance_hourly_limit: StrictInt = Field(default=100, ge=1, le=100)

    def to_limits(self) -> SearchLimits:
        return SearchLimits(**self.model_dump())


class SourceInput(StrictModel):
    source_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    purpose: Purpose
    host: str = Field(min_length=1, max_length=253)
    path_prefix: str = Field(default="/", max_length=500)
    kind: Literal["page", "image"] = "page"
    port: StrictInt | None = Field(default=None, ge=1, le=65535)
    allowed_query_params: list[str] = Field(default_factory=list, max_length=64)
    scheme: Literal["http", "https"] = "https"

    def to_rule(self) -> SearchSourceRule:
        return SearchSourceRule(
            self.source_id,
            self.purpose,
            self.host,
            self.path_prefix,
            self.kind,
            self.port,
            frozenset(self.allowed_query_params),
            self.scheme,
        )


class DeploymentPolicyInput(StrictModel):
    allowed_purposes: list[Purpose] = Field(min_length=1, max_length=5)
    sources: list[SourceInput] = Field(default_factory=list, max_length=100)
    limits: LimitsInput = Field(default_factory=LimitsInput)

    def to_policy(self) -> DeploymentSearchPolicy:
        return DeploymentSearchPolicy(
            frozenset(self.allowed_purposes),
            tuple(source.to_rule() for source in self.sources),
            self.limits.to_limits(),
        )


class GroupPolicyInput(StrictModel):
    allowed_purposes: list[Purpose] | None = None
    source_ids: list[str] | None = Field(default=None, max_length=100)
    limits: LimitsInput | None = None
    meme_mode: Literal["local", "auto", "online"] = "local"

    def to_policy(self) -> GroupSearchPolicy:
        return GroupSearchPolicy(
            None if self.allowed_purposes is None else frozenset(self.allowed_purposes),
            None if self.source_ids is None else frozenset(self.source_ids),
            None if self.limits is None else self.limits.to_limits(),
            self.meme_mode,
        )


class ConsentInput(StrictModel):
    acknowledged: StrictBool


class ProbeInput(StrictModel):
    group_id: StrictInt = Field(gt=0, le=2**63 - 1)


def _deployment_payload(policy: DeploymentSearchPolicy) -> dict:
    return {
        "allowed_purposes": sorted(policy.allowed_purposes),
        "sources": [
            {
                **source.__dict__,
                "allowed_query_params": sorted(source.allowed_query_params),
            }
            for source in policy.sources
        ],
        "limits": policy.limits.__dict__,
        "revision": policy.revision,
    }


def _group_payload(policy: GroupSearchPolicy) -> dict:
    return {
        "allowed_purposes": (
            sorted(policy.allowed_purposes) if policy.allowed_purposes is not None else None
        ),
        "source_ids": sorted(policy.source_ids) if policy.source_ids is not None else None,
        "limits": policy.limits.__dict__ if policy.limits is not None else None,
        "meme_mode": policy.meme_mode,
        "revision": policy.revision,
    }


async def _revoke(request: Request, group_id: int | None = None) -> None:
    runtime = request.app.state.panel_runtime
    if runtime.search_policy is not None:
        await runtime.search_policy.revoke_group_tasks(group_id)
    if runtime.online_memes is not None:
        await runtime.online_memes.expire()


def _record_storage_failure(request: Request, *, writing: bool) -> HTTPException:
    request.app.state.panel_runtime.status.record_diagnostic(
        "storage", "save_failed" if writing else "read_failed"
    )
    return HTTPException(503, "storage_unavailable")


@router.get("/search-policy")
async def get_search_policy(request: Request):
    try:
        policy = await request.app.state.panel_runtime.store.get_deployment_search_policy()
        result = _deployment_payload(policy)
        result[
            "consent_acknowledged"
        ] = await request.app.state.panel_runtime.store.get_search_consent()
        return result
    except (sqlite3.Error, OSError):
        raise _record_storage_failure(request, writing=False) from None


@router.put("/search-policy")
async def set_search_policy(request: Request, body: DeploymentPolicyInput):
    runtime = request.app.state.panel_runtime
    try:
        await runtime.store.set_deployment_search_policy(body.to_policy())
        await _revoke(request)
        return _deployment_payload(await runtime.store.get_deployment_search_policy())
    except (sqlite3.Error, OSError):
        raise _record_storage_failure(request, writing=True) from None
    except ValueError:
        raise HTTPException(422, "invalid_search_policy") from None


@router.get("/groups/{group_id}/search-policy")
async def get_group_search_policy(request: Request, group_id: GroupID):
    if not request.app.state.panel_runtime.settings.group_allowed(group_id):
        raise HTTPException(403, "scope_restricted")
    try:
        policy = await request.app.state.panel_runtime.store.get_group_search_policy(group_id)
        return _group_payload(policy)
    except (sqlite3.Error, OSError):
        raise _record_storage_failure(request, writing=False) from None


@router.put("/groups/{group_id}/search-policy")
async def set_group_search_policy(request: Request, group_id: GroupID, body: GroupPolicyInput):
    runtime = request.app.state.panel_runtime
    if not runtime.settings.group_allowed(group_id):
        raise HTTPException(403, "scope_restricted")
    try:
        await runtime.store.set_group_search_policy(group_id, body.to_policy())
        await _revoke(request, group_id)
        return _group_payload(await runtime.store.get_group_search_policy(group_id))
    except (sqlite3.Error, OSError):
        raise _record_storage_failure(request, writing=True) from None
    except ValueError:
        raise HTTPException(422, "invalid_group_search_policy") from None


@router.post("/search-consent")
async def set_search_consent(request: Request, body: ConsentInput):
    runtime = request.app.state.panel_runtime
    try:
        await runtime.features.set_search_consent(
            request.state.panel_session.owner_id, body.acknowledged
        )
        return {"acknowledged": await runtime.store.get_search_consent()}
    except PermissionError:
        raise HTTPException(403, "owner_required") from None
    except (sqlite3.Error, OSError):
        raise _record_storage_failure(request, writing=True) from None


@router.get("/search-status")
async def get_search_status(request: Request):
    return dict(request.app.state.panel_search_status)


@router.post("/search-probe")
async def search_probe(request: Request, body: ProbeInput):
    from aling.services.search import SearchError
    from aling.services.search_policy import SearchPolicyDenied

    runtime = request.app.state.panel_runtime
    status = request.app.state.panel_search_status
    group_id = body.group_id
    if not runtime.settings.group_allowed(group_id):
        raise HTTPException(403, "scope_restricted")
    if runtime.search is None or runtime.search_policy is None:
        raise HTTPException(503, "search_unavailable")
    context = None
    try:
        state = await runtime.features.state(group_id, "search")
        if not state.effective:
            raise HTTPException(409, "feature_unavailable")
        if not await runtime.search_policy.search_ready(group_id):
            raise HTTPException(409, "search_policy_unavailable")
        context = await runtime.search_policy.create_task_from_panel(
            request.state.panel_session, group_id
        )
        result = await runtime.search.search(
            context,
            "python documentation",
            runtime.search_policy.classifier,
            TaskBudget.start(),
        )
        status.update(
            internal_reachable=True,
            json_enabled=True,
            upstream_results=bool(result.results),
            last_verified=result.retrieved_at.isoformat(),
            category="general",
            result_count=len(result.results),
            code="success" if result.results else "no_results",
            exception_type=None,
        )
        return {
            "internal_reachable": True,
            "json_enabled": True,
            "upstream_results": bool(result.results),
            "last_verified": result.retrieved_at.isoformat(),
            "category": "general",
            "result_count": len(result.results),
        }
    except SearchError as exc:
        if exc.code in {
            "global_off",
            "group_off",
            "scope_restricted",
            "topic_not_allowed",
            "intent_uncertain",
            "classification_unavailable",
            "source_not_allowed",
            "policy_changed",
        }:
            raise HTTPException(409, "search_policy_unavailable") from None
        if exc.code == "no_results":
            status.update(
                internal_reachable=True,
                json_enabled=True,
                upstream_results=False,
                last_verified=datetime.now(UTC).isoformat(),
                category="general",
                result_count=0,
                code="no_results",
                exception_type=None,
            )
            raise HTTPException(502, "no_results") from None
        reached = exc.code in {"json_disabled", "upstream_failed"}
        status.update(
            internal_reachable=(
                True if reached else False if exc.code == "service_unreachable" else None
            ),
            json_enabled=(
                False
                if exc.code == "json_disabled"
                else True
                if exc.code == "upstream_failed"
                else None
            ),
            upstream_results=False if exc.code == "upstream_failed" else None,
            last_verified=datetime.now(UTC).isoformat(),
            category="general",
            result_count=0,
            code=exc.code,
            exception_type=None,
        )
        runtime.status.record_diagnostic("search", "probe_failed")
        raise HTTPException(502, "search_probe_failed") from None
    except SearchPolicyDenied:
        raise HTTPException(409, "search_policy_unavailable") from None
    except (sqlite3.Error, OSError):
        raise _record_storage_failure(request, writing=False) from None
    finally:
        if context is not None:
            runtime.search_policy.revoke_task(context.task_id)
