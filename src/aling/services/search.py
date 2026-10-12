"""Fixed-origin, bounded SearXNG access and policy-gated search orchestration."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from html import unescape
from time import monotonic

import httpx

from aling import __version__
from aling.core.config import Settings
from aling.core.logging import log
from aling.core.storage import SearchLimits
from aling.services.http import read_limited
from aling.services.search_budget import (
    AdmissionRejected,
    BudgetExceeded,
    SearchAdmission,
    SearchQuotaLimits,
    TaskBudget,
)
from aling.services.search_policy import (
    IntentClassifier,
    SearchPolicyDenied,
    SearchPolicyService,
    SearchTaskContext,
)


class SearchError(RuntimeError):
    """A safe, stable search failure code without upstream details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    content: str


@dataclass(frozen=True)
class AuthorizedSearchHit:
    result_id: str
    source_id: str
    title: str
    url: str
    content: str


@dataclass(frozen=True)
class AuthorizedSearchResults:
    results: tuple[AuthorizedSearchHit, ...]
    retrieved_at: datetime


_LANGUAGES = {"auto", "zh", "zh-CN", "en", "en-US"}
_TEXT_TAG = re.compile(r"<[^>]{0,200}>")


class SearxngClient:
    """POST-only SearXNG client with fixed origin and bounded JSON parsing."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock=monotonic,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self._client = httpx.AsyncClient(
            base_url=settings.search_origin,
            timeout=httpx.Timeout(settings.search_timeout_seconds),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
            limits=httpx.Limits(
                max_connections=settings.search_max_connections,
                max_keepalive_connections=settings.search_max_connections,
            ),
        )

    async def search(
        self,
        query: str,
        *,
        category: str,
        language: str,
        safe_search: int,
        limit: int,
        budget: TaskBudget,
    ) -> list[SearchHit]:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 100
            or any(ord(char) < 32 or ord(char) == 127 for char in query)
            or category not in {"general", "images"}
            or language not in _LANGUAGES
            or type(safe_search) is not int
            or safe_search != 2
            or type(limit) is not int
            or not 1 <= limit <= 3
        ):
            raise SearchError("upstream_failed")
        timeout = min(
            self.settings.search_timeout_seconds,
            budget.limits.request_timeout_seconds,
            budget.remaining_work_seconds(),
        )
        if timeout <= 0:
            raise SearchError("budget_exceeded")
        try:
            budget.reserve_external_operation("search")
            async with asyncio.timeout(timeout):
                async with self._client.stream(
                    "POST",
                    "/search",
                    data={
                        "q": query,
                        "categories": category,
                        "language": language,
                        "safesearch": str(safe_search),
                        "format": "json",
                        "pageno": "1",
                    },
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "User-Agent": f"ALING/{__version__}",
                    },
                    timeout=httpx.Timeout(timeout),
                ) as response:
                    if response.is_redirect:
                        raise SearchError("upstream_failed")
                    if response.status_code == 403:
                        raise SearchError("json_disabled")
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0]
                    if content_type.strip().lower() != "application/json":
                        raise SearchError("upstream_failed")
                    body = await read_limited(response, self.settings.search_max_bytes)
                    payload = json.loads(body)
        except SearchError:
            raise
        except BudgetExceeded:
            raise SearchError("budget_exceeded") from None
        except httpx.HTTPStatusError as exc:
            code = "json_disabled" if exc.response.status_code == 403 else "upstream_failed"
            raise SearchError(code) from None
        except (
            httpx.HTTPError,
            TimeoutError,
            ValueError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            log.warning("SearXNG request failed ({})", type(exc).__name__)
            raise SearchError("service_unreachable") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise SearchError("upstream_failed")
        hits = []
        for item in payload["results"][:limit]:
            if not isinstance(item, dict):
                raise SearchError("upstream_failed")
            title, url = item.get("title"), item.get("url")
            content = item.get("content", "")
            if (
                not isinstance(title, str)
                or not title.strip()
                or len(title) > 2000
                or not isinstance(url, str)
                or not url
                or len(url) > 2048
                or not isinstance(content, str)
                or len(content) > 10000
            ):
                raise SearchError("upstream_failed")
            hits.append(SearchHit(_clean_text(title, 300), url, _clean_text(content, 600)))
        return hits

    async def aclose(self) -> None:
        await self._client.aclose()


class SearchService:
    def __init__(
        self,
        policy: SearchPolicyService,
        client: SearxngClient,
        *,
        admission: SearchAdmission | None = None,
        language: str = "auto",
    ) -> None:
        self.policy = policy
        self.client = client
        self.admission = admission or SearchAdmission()
        self.language = language

    async def search(
        self,
        context: SearchTaskContext,
        text: str,
        classifier: IntentClassifier | None,
        budget: TaskBudget,
    ) -> AuthorizedSearchResults:
        try:
            query = await self.policy.preflight(context, text, "search")
            deployment = await self.policy.store.get_deployment_search_policy()
            group = await self.policy.store.get_group_search_policy(context.group_id)
            effective_limits = SearchLimits(
                **{
                    item.name: min(
                        getattr(deployment.limits, item.name),
                        getattr(group.limits, item.name)
                        if group.limits is not None
                        else getattr(deployment.limits, item.name),
                    )
                    for item in fields(SearchLimits)
                }
            )
            budget.apply_policy_limits(effective_limits)
            async with self.admission.slot(
                group_id=context.group_id,
                user_id=context.user_id,
                limits=SearchQuotaLimits.from_policy(effective_limits),
            ):
                authorized = await self.policy.authorize_online(
                    context,
                    query,
                    "search",
                    budget=budget,
                    classifier=classifier,
                )
                hits = await self.client.search(
                    authorized.normalized_query or query,
                    category="general",
                    language=self.language,
                    safe_search=2,
                    limit=budget.limits.max_results,
                    budget=budget,
                )
                if not hits:
                    raise SearchError("no_results")
                results = []
                for hit in hits:
                    for source_id in authorized.source_ids:
                        try:
                            result_id = await self.policy.register_result(
                                authorized, hit.url, source_id
                            )
                        except SearchPolicyDenied:
                            continue
                        results.append(
                            AuthorizedSearchHit(
                                result_id,
                                source_id,
                                _clean_text(hit.title, 200),
                                await self._validated_search_url(authorized, hit.url, source_id),
                                _clean_text(hit.content, 500),
                            )
                        )
                        break
                    if len(results) >= 3:
                        break
                if hits and not results:
                    raise SearchError("no_results")
                return AuthorizedSearchResults(tuple(results), datetime.now(UTC))
        except AdmissionRejected:
            raise SearchError("rate_limited") from None
        except BudgetExceeded:
            raise SearchError("budget_exceeded") from None
        except SearchPolicyDenied as exc:
            raise SearchError(_policy_error(exc)) from None

    async def _validated_search_url(
        self, context: SearchTaskContext, url: str, source_id: str
    ) -> str:
        await self.policy.revalidate(context, "search", source_id)
        assert context.purpose is not None
        return await self.policy.validate_result_url(url, context.purpose, source_id)


def _clean_text(value: str, limit: int) -> str:
    value = unescape(_TEXT_TAG.sub(" ", value))
    value = " ".join("".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in value).split())
    return value[:limit]


def _policy_error(exc: SearchPolicyDenied) -> str:
    message = str(exc)
    if "budget" in message.lower() or "deadline" in message.lower():
        return "budget_exceeded"
    if "classification" in message.lower() or "intent" in message.lower():
        unavailable = "unavailable" in message.lower() or "failed" in message.lower()
        return "classification_unavailable" if unavailable else "intent_uncertain"
    if "policy changed" in message.lower():
        return "policy_changed"
    if "source" in message.lower():
        return "source_not_allowed"
    if "already searched" in message.lower() or "limit" in message.lower():
        return "budget_exceeded"
    return "topic_not_allowed"
