"""Fail-closed authorization for controlled online search operations."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import secrets
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from time import monotonic
from typing import Any, Protocol
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from nonebot.adapters.onebot.v11 import GroupMessageEvent

from aling.core.config import Settings
from aling.core.storage import (
    DeploymentSearchPolicy,
    GroupSearchPolicy,
    GroupSettingsStore,
    SearchSourceRule,
)
from aling.services.features import FeatureService
from aling.services.web import public_ip
from aling.webui.auth import PanelAuth, PanelSession


class SearchPurpose(StrEnum):
    TECHNICAL = "technical"
    ORDINARY_MEME = "ordinary_meme"
    GAMING = "gaming"
    HOBBY = "hobby"
    PRACTICAL = "practical"
    UNKNOWN = "unknown"


class SearchPolicyDenied(PermissionError):
    """The request is outside the currently authorized search policy."""


class IntentClassifier(Protocol):
    async def classify(self, text: str, *, budget: Any = None) -> SearchPurpose | None: ...


@dataclass(frozen=True)
class SearchTaskContext:
    task_id: str
    query_id: str
    group_id: int
    user_id: int
    purpose: SearchPurpose | None = None
    normalized_query: str | None = None
    policy_revision: tuple[int, int] | None = None
    source_ids: frozenset[str] = frozenset()
    authorized_at: float | None = None


@dataclass
class _TaskRecord:
    context: SearchTaskContext
    expires_at: float
    event: GroupMessageEvent | None = None
    event_identity: tuple[int, int, int] | None = None
    session: PanelSession | None = None
    panel_auth: PanelAuth | None = None
    source_query: str = ""
    classification: SearchPurpose | None = None
    classification_attempted: bool = False
    search_used: bool = False
    operation: str | None = None
    results: dict[str, tuple[str, str, str]] | None = None
    issued: dict[int, SearchTaskContext] | None = None
    lock: asyncio.Lock | None = None


_MAX_TASKS = 2048
_MAX_QUERY = 100
_QUERY_TIMEOUT = 10
_FORBIDDEN_TERMS = re.compile(
    r"(?:成人|色情|裸照|血腥|尸体|诈骗|赃物|赃款|盗号|窃取|密码|口令|验证码|"
    r"api[_ -]?key|access[_ -]?token|authorization|bearer|他人的手机号|"
    r"他人.*(?:住址|位置|联系方式)|"
    r"批量(?:搜索|查询|下载|抓取)|(?:搜索|查询|下载|抓取).{0,8}批量|绕过.*(?:登录|限制)|"
    r"(?:qq|企鹅号).{0,6}\d{6,}|\d{6,}.{0,6}(?:qq|企鹅号)|"
    r"(?:登录|登陆).{0,8}(?:受限|限制|账号)|(?:分|搜|查)(?:十|[2-9]\d*)页)",
    re.IGNORECASE,
)
_SENSITIVE_KEY = re.compile(
    r"(?:token|secret|password|passwd|auth|signature|sig|api.?key|access.?key|session|cookie|credential|bearer|private.?key)",
    re.I,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:(?:api[_ -]?key|access[_ -]?token|token|secret|password|cookie|session)\s*[=:]|bearer\s+[a-z0-9._~+/-]{4,}|https?://[^\s:@/]+:[^\s@/]+@)",
    re.I,
)
_QUERY_OPERATOR = re.compile(
    r"(?<![\w])(?:(?:site|filetype|inurl|intitle|engine)\s*:|"
    r"(?:engines?|categories|pageno|page|language|safesearch)\s*=|!{1,2}\s*[a-z][a-z0-9_-]*)",
    re.I,
)
_FORBIDDEN_ENGLISH = re.compile(
    r"\b(?:dox(?:xing)?|steal(?:ing)?\s+(?:credentials|passwords?|accounts?)|"
    r"private\s+(?:address|location|phone)|bulk\s+(?:download|scrap|crawl)|"
    r"porn(?:ography)?|gore|illegal\s+(?:purchase|trade))\b",
    re.I,
)
_PRIVATE_CONTACT = re.compile(r"(?:\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|(?<!\d)1[3-9]\d{9}(?!\d))", re.I)
_QUERY_PARAM = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class SearchPolicyService:
    """Creates opaque, short-lived tasks and rechecks live policy on each use."""

    def __init__(
        self,
        features: FeatureService,
        *,
        classifier: IntentClassifier | None = None,
        panel_auth: PanelAuth | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.features = features
        self.store: GroupSettingsStore = features.store
        self.settings: Settings = features.settings
        self.classifier = classifier
        self.panel_auth = panel_auth
        self.clock = clock
        self._tasks: OrderedDict[str, _TaskRecord] = OrderedDict()

    async def create_task_from_event(self, event: GroupMessageEvent) -> SearchTaskContext:
        if not isinstance(event, GroupMessageEvent) or event.user_id == event.self_id:
            raise SearchPolicyDenied("A real group user is required")
        if not self.settings.group_allowed(event.group_id) or event.user_id <= 0:
            raise SearchPolicyDenied("Group or user is outside the configured scope")
        try:
            source_query = event.original_message.extract_plain_text()
        except (AttributeError, TypeError):
            raise SearchPolicyDenied("Invalid group event") from None
        context = await self._new_context(event.group_id, event.user_id, source_query)
        record = self._tasks[context.task_id]
        record.event = event
        record.event_identity = (event.group_id, event.user_id, event.self_id)
        return context

    async def create_task_from_panel(
        self, owner_session: PanelSession, group_id: int
    ) -> SearchTaskContext:
        if self.panel_auth is None or not isinstance(owner_session, PanelSession):
            raise SearchPolicyDenied("Authenticated panel owner required")
        try:
            current = self.panel_auth.authenticate(owner_session.session_id)
        except (PermissionError, AttributeError):
            raise SearchPolicyDenied("Panel session is invalid") from None
        if current is not owner_session or current.owner_id not in self.settings.superusers:
            raise SearchPolicyDenied("Panel session is invalid")
        if type(group_id) is not int or group_id <= 0 or not self.settings.group_allowed(group_id):
            raise SearchPolicyDenied("Group is outside the configured scope")
        context = await self._new_context(group_id, current.owner_id, "")
        record = self._tasks[context.task_id]
        record.session = current
        record.panel_auth = self.panel_auth
        return context

    async def _new_context(
        self, group_id: int, user_id: int, source_query: str
    ) -> SearchTaskContext:
        deployment = await self.store.get_deployment_search_policy()
        group = await self.store.get_group_search_policy(group_id)
        context = SearchTaskContext(
            task_id=secrets.token_urlsafe(24),
            query_id=secrets.token_urlsafe(18),
            group_id=group_id,
            user_id=user_id,
            policy_revision=(deployment.revision, group.revision),
        )
        record = _TaskRecord(
            context=context,
            expires_at=self.clock()
            + min(
                deployment.limits.task_seconds,
                group.limits.task_seconds
                if group.limits is not None
                else deployment.limits.task_seconds,
            ),
            source_query=source_query,
            results={},
            issued={id(context): context},
            lock=asyncio.Lock(),
        )
        self._tasks[context.task_id] = record
        self._tasks.move_to_end(context.task_id)
        self._prune_tasks()
        return context

    def _prune_tasks(self) -> None:
        now = self.clock()
        for task_id, record in list(self._tasks.items()):
            if record.expires_at <= now:
                self._tasks.pop(task_id, None)
        while len(self._tasks) > _MAX_TASKS:
            self._tasks.popitem(last=False)

    def _record(self, context: SearchTaskContext) -> _TaskRecord:
        self._prune_tasks()
        if not isinstance(context, SearchTaskContext):
            raise SearchPolicyDenied("Unissued task handle")
        record = self._tasks.get(context.task_id)
        if record is None or record.issued is None or record.issued.get(id(context)) is not context:
            raise SearchPolicyDenied("Unissued or expired task handle")
        if context.group_id != record.context.group_id or context.user_id != record.context.user_id:
            raise SearchPolicyDenied("Task principal changed")
        if record.event is not None:
            event = record.event
            if (event.group_id, event.user_id, event.self_id) != record.event_identity:
                raise SearchPolicyDenied("Group event principal changed")
        if record.session is not None and record.panel_auth is not None:
            try:
                current = record.panel_auth.authenticate(record.session.session_id)
            except PermissionError:
                raise SearchPolicyDenied("Panel session expired or revoked") from None
            if current is not record.session or current.owner_id != context.user_id:
                raise SearchPolicyDenied("Panel session changed")
        return record

    async def _policies(
        self, context: SearchTaskContext
    ) -> tuple[DeploymentSearchPolicy, GroupSearchPolicy]:
        return (
            await self.store.get_deployment_search_policy(),
            await self.store.get_group_search_policy(context.group_id),
        )

    @staticmethod
    def _purpose_sources(
        deployment: DeploymentSearchPolicy,
        group: GroupSearchPolicy,
        purpose: SearchPurpose,
        *,
        kind: str | None = "page",
    ) -> frozenset[str]:
        if purpose.value not in deployment.allowed_purposes:
            return frozenset()
        if group.allowed_purposes is not None and purpose.value not in group.allowed_purposes:
            return frozenset()
        selected = []
        for source in deployment.sources:
            if source.purpose != purpose.value or (kind is not None and source.kind != kind):
                continue
            if group.source_ids is not None and source.source_id not in group.source_ids:
                continue
            selected.append(source.source_id)
        return frozenset(selected)

    async def _gate(
        self, context: SearchTaskContext, operation: str
    ) -> tuple[DeploymentSearchPolicy, GroupSearchPolicy]:
        self._record(context)
        if operation not in {"search", "online_meme", "read", "image"}:
            raise SearchPolicyDenied("Unknown online operation")
        deployment, group = await self._policies(context)
        if context.policy_revision != (deployment.revision, group.revision):
            raise SearchPolicyDenied("Search policy changed; start a new task")
        if not self.settings.group_allowed(context.group_id):
            raise SearchPolicyDenied("Group is outside the configured scope")
        if not await self.store.get_search_consent():
            raise SearchPolicyDenied("External search data-flow acknowledgement is missing")
        feature = "online_meme" if operation in {"online_meme", "image"} else "search"
        if not await self.features.enabled(context.group_id, feature):
            raise SearchPolicyDenied("Online feature is disabled for this group")
        if operation == "read" and not await self.features.enabled(context.group_id, "web"):
            raise SearchPolicyDenied("Web reading is disabled for this group")
        if context.purpose is not None:
            if not self._purpose_sources(deployment, group, context.purpose):
                # Meme tasks use image rules rather than page rules.
                if not (
                    context.purpose is SearchPurpose.ORDINARY_MEME
                    and self._purpose_sources(deployment, group, context.purpose, kind="image")
                ):
                    raise SearchPolicyDenied("No approved source remains for this purpose")
        else:
            kind = "image" if operation == "online_meme" else "page"
            permitted = deployment.allowed_purposes
            if group.allowed_purposes is not None:
                permitted = permitted & group.allowed_purposes
            if not any(
                self._purpose_sources(deployment, group, SearchPurpose(purpose), kind=kind)
                for purpose in permitted
            ):
                raise SearchPolicyDenied("No approved source remains for this operation")
        # Storage reads and feature checks yield; a concurrent policy edit must
        # not leave the earlier snapshot usable when authorization returns.
        current_deployment, current_group = await self._policies(context)
        if context.policy_revision != (current_deployment.revision, current_group.revision):
            raise SearchPolicyDenied("Search policy changed; start a new task")
        self._record(context)
        return deployment, group

    async def preflight(self, context: SearchTaskContext, text: str, operation: str) -> str:
        deployment, _ = await self._gate(context, operation)
        query = _normalize_query(text)
        record = self._record(context)
        if record.event is not None and (
            not record.source_query or _event_query(record.source_query) != query
        ):
            # The signed task can search the exact user request only. The owner panel
            # intentionally creates a blank request and supplies its authenticated input.
            raise SearchPolicyDenied("Search query must match the current group request")
        if len(query) > _MAX_QUERY:
            raise SearchPolicyDenied("Search query is too long")
        if record.source_query and len(_event_query(record.source_query)) > _MAX_QUERY:
            raise SearchPolicyDenied("Original request is too long")
        if _unsafe_query(query):
            raise SearchPolicyDenied("Search query is outside the approved scope")
        if record.context.normalized_query and record.context.normalized_query != query:
            raise SearchPolicyDenied("A task cannot change its frozen query")
        if not deployment.sources:
            raise SearchPolicyDenied("No online source has been approved")
        return query

    async def classify_once(
        self, context: SearchTaskContext, text: str, *, budget: Any = None
    ) -> SearchPurpose:
        query = await self.preflight(
            context,
            text,
            self._record(context).operation
            or ("online_meme" if context.purpose is SearchPurpose.ORDINARY_MEME else "search"),
        )
        record = self._record(context)
        await self._classify(record, query, budget)
        assert record.classification is not None
        return record.classification

    async def _classify(
        self,
        record: _TaskRecord,
        query: str,
        budget: Any,
        operation: str | None = None,
        classifier: IntentClassifier | None = None,
    ) -> None:
        classifier = classifier or self.classifier
        if classifier is None:
            record.classification_attempted = True
            raise SearchPolicyDenied("Intent classification is unavailable")
        if not record.lock:
            raise SearchPolicyDenied("Task is invalid")
        async with record.lock:
            if record.classification_attempted:
                if record.classification is None:
                    raise SearchPolicyDenied("Search classification was already denied")
                if record.context.normalized_query != query:
                    raise SearchPolicyDenied("A task cannot change its frozen query")
                return
            context = record.context
            operation = (
                operation
                or record.operation
                or ("online_meme" if context.purpose is SearchPurpose.ORDINARY_MEME else "search")
            )
            deployment, group = await self._gate(context, operation)
            record.classification_attempted = True
            try:
                request_timeout = min(
                    _QUERY_TIMEOUT,
                    deployment.limits.request_timeout_seconds,
                    group.limits.request_timeout_seconds
                    if group.limits is not None
                    else deployment.limits.request_timeout_seconds,
                    max(0, record.expires_at - self.clock()),
                )
                budget_remaining = getattr(budget, "remaining_work_seconds", None)
                if callable(budget_remaining):
                    request_timeout = min(request_timeout, budget_remaining())
                if request_timeout <= 0:
                    record.classification = None
                    raise SearchPolicyDenied("Task work budget is exhausted")
                async with asyncio.timeout(request_timeout):
                    purpose = await classifier.classify(query, budget=budget)
            except TimeoutError:
                record.classification = None
                raise SearchPolicyDenied("Intent classification failed or timed out") from None
            except SearchPolicyDenied:
                record.classification = None
                raise
            except asyncio.CancelledError:
                record.classification = None
                raise
            except Exception:
                record.classification = None
                raise SearchPolicyDenied("Intent classification is unavailable") from None
            if not isinstance(purpose, SearchPurpose) or purpose is SearchPurpose.UNKNOWN:
                record.classification = None
                raise SearchPolicyDenied("Intent is uncertain or malformed")
            if purpose.value not in deployment.allowed_purposes or (
                group.allowed_purposes is not None and purpose.value not in group.allowed_purposes
            ):
                record.classification = None
                raise SearchPolicyDenied("Intent is not permitted by the current policy")
            kind = None if operation == "online_meme" else "page"
            if operation == "online_meme" and (
                purpose is not SearchPurpose.ORDINARY_MEME
                or not self._purpose_sources(deployment, group, purpose, kind="image")
            ):
                raise SearchPolicyDenied("Online memes require an approved image source")
            source_ids = self._purpose_sources(deployment, group, purpose, kind=kind)
            if not source_ids:
                record.classification = None
                raise SearchPolicyDenied("No approved source remains for this purpose")
            # Re-check consent, gates and policy after the external model call.
            live_deployment, live_group = await self._gate(context, operation)
            if (live_deployment.revision, live_group.revision) != context.policy_revision:
                record.classification = None
                raise SearchPolicyDenied("Search policy changed during classification")
            record.classification = purpose
            record.context = replace(
                context,
                purpose=purpose,
                normalized_query=query,
                source_ids=source_ids,
            )
            self._issue(record, record.context)

    def _issue(self, record: _TaskRecord, context: SearchTaskContext) -> SearchTaskContext:
        if record.issued is None:
            record.issued = {}
        record.issued[id(context)] = context
        record.context = context
        return context

    async def authorize_online(
        self,
        context: SearchTaskContext,
        text: str,
        operation: str,
        *,
        budget: Any = None,
        classifier: IntentClassifier | None = None,
    ) -> SearchTaskContext:
        if operation not in {"search", "online_meme"}:
            raise SearchPolicyDenied("Only a search operation can create online authorization")
        query = await self.preflight(context, text, operation)
        record = self._record(context)
        if record.operation is None:
            record.operation = operation
        elif record.operation != operation:
            raise SearchPolicyDenied("A task cannot change its online operation")
        if not record.lock:
            raise SearchPolicyDenied("Task is invalid")
        async with record.lock:
            self._record(context)
            if record.search_used:
                raise SearchPolicyDenied("This task has already searched")
            if record.context.normalized_query and record.context.normalized_query != query:
                raise SearchPolicyDenied("A task cannot change its frozen query")
            if record.classification_attempted and record.classification is None:
                raise SearchPolicyDenied("Search classification was already denied")
        if not record.classification_attempted:
            await self._classify(record, query, budget, operation, classifier)
        if record.classification is None:
            raise SearchPolicyDenied("Intent classification was denied")
        deployment, group = await self._gate(record.context, operation)
        purpose = record.classification
        kind = None if operation == "online_meme" else "page"
        sources = self._purpose_sources(deployment, group, purpose, kind=kind)
        if not sources:
            raise SearchPolicyDenied("No approved source remains for this purpose")
        async with record.lock:
            self._record(record.context)
            if record.search_used:
                raise SearchPolicyDenied("This task has already searched")
            record.search_used = True
            authorized = replace(
                record.context,
                purpose=purpose,
                normalized_query=query,
                policy_revision=(deployment.revision, group.revision),
                source_ids=sources,
                authorized_at=self.clock(),
            )
            return self._issue(record, authorized)

    async def revalidate(
        self, context: SearchTaskContext, operation: str, source_id: str | None = None
    ) -> None:
        deployment, group = await self._gate(context, operation)
        if context.authorized_at is None or context.purpose is None:
            raise SearchPolicyDenied("Task has no online authorization")
        record = self._record(context)
        if operation in {"read", "image"}:
            if record.operation != ("online_meme" if operation == "image" else "search"):
                raise SearchPolicyDenied("Result operation is outside this task's authorization")
            result = (record.results or {}).get(source_id)
            if result is None:
                raise SearchPolicyDenied("Reading requires an approved task result ID")
            expected_kind = "image" if operation == "image" else "page"
            if result[2] != expected_kind:
                raise SearchPolicyDenied("Result kind is not valid for this operation")
        elif operation != record.operation:
            raise SearchPolicyDenied("Task operation cannot change")
        if source_id is not None:
            result = (record.results or {}).get(source_id)
            if result is None:
                if source_id not in context.source_ids:
                    raise SearchPolicyDenied("Source/result is not authorized for this task")
            else:
                source_id = result[1]
            kind = (
                None
                if operation == "online_meme"
                else ("image" if operation == "image" else "page")
            )
            if source_id not in self._purpose_sources(
                deployment, group, context.purpose, kind=kind
            ):
                raise SearchPolicyDenied("Source authorization has been revoked")
        elif not context.source_ids:
            raise SearchPolicyDenied("No source is authorized")

    async def validate_result_url(self, url: str, purpose: SearchPurpose, source_id: str) -> str:
        if not isinstance(purpose, SearchPurpose) or purpose is SearchPurpose.UNKNOWN:
            raise SearchPolicyDenied("Unknown purpose")
        deployment = await self.store.get_deployment_search_policy()
        source = next((item for item in deployment.sources if item.source_id == source_id), None)
        if source is None or source.purpose != purpose.value:
            raise SearchPolicyDenied("Source is not approved for this purpose")
        return _canonical_source_url(url, source)

    async def register_result(self, context: SearchTaskContext, url: str, source_id: str) -> str:
        await self.revalidate(
            context,
            self._record(context).operation,
            source_id,
        )
        if source_id not in context.source_ids:
            raise SearchPolicyDenied("Source is not authorized for this task")
        canonical = await self.validate_result_url(url, context.purpose, source_id)
        record = self._record(context)
        result_id = secrets.token_urlsafe(18)
        deployment = await self.store.get_deployment_search_policy()
        source = next((item for item in deployment.sources if item.source_id == source_id), None)
        if source is None:
            raise SearchPolicyDenied("Source authorization has been revoked")
        await self.revalidate(context, record.operation, source_id)
        assert record.results is not None
        record.results[result_id] = (canonical, source_id, source.kind)
        return result_id

    async def resolve_result(
        self, context: SearchTaskContext, result_id: str, *, operation: str = "read"
    ) -> str:
        record = self._record(context)
        item = (record.results or {}).get(result_id)
        if item is None:
            raise SearchPolicyDenied("Result is not bound to this task")
        url, source_id, _ = item
        await self.revalidate(context, operation, result_id)
        canonical = await self.validate_result_url(url, context.purpose, source_id)
        if canonical != url:
            raise SearchPolicyDenied("Result URL changed after validation")
        return url

    def revoke_task(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)


def _normalize_query(value: str) -> str:
    if not isinstance(value, str):
        raise SearchPolicyDenied("Query must be text")
    normalized = unicodedata.normalize("NFKC", value)
    if any(
        ord(char) == 0 or (ord(char) < 32 and char not in "\t") or ord(char) == 127
        for char in normalized
    ):
        raise SearchPolicyDenied("Query contains control characters")
    return " ".join(normalized.split())


def _event_query(text: str) -> str:
    """Remove known entry syntax only, preserving the user's entire request."""
    text = _normalize_query(text)
    for command in ("/search ", "/meme "):
        if text.startswith(command):
            query = text[len(command) :]
            if command == "/meme ":
                for mode in ("local", "auto", "online"):
                    option = f"--mode {mode} "
                    if query.startswith(option):
                        return query[len(option) :]
            return query
    return text


def _unsafe_query(query: str) -> bool:
    if not query or len(query) > _MAX_QUERY or "\n" in query or ";" in query:
        return True
    if (
        _FORBIDDEN_TERMS.search(query)
        or _FORBIDDEN_ENGLISH.search(query)
        or _SENSITIVE_VALUE.search(query)
        or _PRIVATE_CONTACT.search(query)
        or _QUERY_OPERATOR.search(query)
    ):
        return True
    if re.search(r"(?:\bsite\s*：|！{1,2}[a-z]|[！!]{1,2}[a-z])", query, re.I):
        return True
    if query.startswith("https://") or query.startswith("http://"):
        return True
    return False


def _canonical_host(value: str) -> str:
    host = value.rstrip(".").lower()
    if not host or value.endswith("..") or any(char in host for char in "/\\@:%"):
        raise SearchPolicyDenied("Invalid source host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not public_ip(host):
            raise SearchPolicyDenied("Private and non-global hosts are denied")
        return address.compressed.lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise SearchPolicyDenied("Invalid internationalized host") from None
    if host in {"localhost", "local", "search"} or host.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise SearchPolicyDenied("Local hosts are not approved")
    labels = host.split(".")
    if len(labels) < 2 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
    ):
        raise SearchPolicyDenied("Invalid DNS host")
    return host


def _canonical_path(path: str) -> str:
    if not path.startswith("/") or "\\" in path or any(ord(char) < 33 for char in path):
        raise SearchPolicyDenied("Invalid URL path")
    # Encoded separators can change routing at a proxy or origin even when a
    # single decode appears to stay inside the approved prefix.
    if re.search(r"%(?:2f|5c|00)", path, re.I):
        raise SearchPolicyDenied("Encoded path separators are denied")
    if re.search(r"%(?![0-9a-fA-F]{2})", path):
        raise SearchPolicyDenied("Malformed path encoding")
    try:
        current = unquote(path, errors="strict")
    except UnicodeError:
        raise SearchPolicyDenied("Malformed path encoding") from None
    if "%" in current:
        raise SearchPolicyDenied("Nested path encoding is denied")
    if any(char in current for char in "\\\x00") or "//" in current:
        raise SearchPolicyDenied("Ambiguous URL path")
    if any(part in {".", ".."} for part in current.split("/")):
        raise SearchPolicyDenied("Path traversal is denied")
    if any(
        ord(char) < 32 or ord(char) == 127 or unicodedata.category(char) in {"Cf", "Cs"}
        for char in current
    ):
        raise SearchPolicyDenied("Control characters are denied")
    return quote(current, safe="/!$&'()*+,;=:@-._~")


def _canonical_source_url(value: str, source: SearchSourceRule) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or "#" in value
        or "\\" in value
        or any(ord(char) < 33 or ord(char) == 127 for char in value)
    ):
        raise SearchPolicyDenied("Malformed result URL")
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise SearchPolicyDenied("Only HTTP(S) source URLs are allowed")
        if parts.username is not None or parts.password is not None:
            raise SearchPolicyDenied("URL credentials are denied")
        host = _canonical_host(parts.hostname)
        port = parts.port
    except (ValueError, UnicodeError):
        raise SearchPolicyDenied("Malformed result URL") from None
    if parts.scheme.lower() != source.scheme:
        raise SearchPolicyDenied("Result scheme is not approved")
    approved_host = _canonical_host(source.host)
    if host != approved_host:
        raise SearchPolicyDenied("Result host is not approved")
    standard_port = 443 if parts.scheme.lower() == "https" else 80
    if source.port is None:
        if port not in (None, standard_port):
            raise SearchPolicyDenied("Non-standard port is not approved")
    elif port != source.port:
        raise SearchPolicyDenied("Port is not approved")
    path = _canonical_path(parts.path or "/")
    try:
        prefix = _canonical_path(source.path_prefix)
    except (TypeError, ValueError):
        raise SearchPolicyDenied("Invalid configured source path") from None
    if prefix != "/" and not (path == prefix or path.startswith(prefix.rstrip("/") + "/")):
        raise SearchPolicyDenied("Result path is outside the approved prefix")
    try:
        if re.search(r"%(?![0-9a-fA-F]{2})", parts.query):
            raise ValueError("Malformed query encoding")
        query = parse_qsl(
            parts.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=20,
            errors="strict",
        )
    except ValueError:
        raise SearchPolicyDenied("Malformed result query") from None
    seen: set[str] = set()
    for key, item in query:
        lowered = key.lower()
        if not _QUERY_PARAM.fullmatch(key) or lowered in seen:
            raise SearchPolicyDenied("Malformed or duplicate query parameter")
        seen.add(lowered)
        decoded_item = item
        if "%" in decoded_item:
            raise SearchPolicyDenied("Nested query encoding is denied")
        if (
            _SENSITIVE_KEY.search(lowered)
            or _SENSITIVE_KEY.search(decoded_item)
            or _SENSITIVE_VALUE.search(decoded_item)
            or "/" in decoded_item
            or "\\" in decoded_item
        ):
            raise SearchPolicyDenied("Sensitive query parameter is denied")
        if key not in source.allowed_query_params:
            raise SearchPolicyDenied("Query parameter is not approved")
        if len(item) > 200 or any(
            ord(char) < 32 or ord(char) == 127 or unicodedata.category(char) in {"Cf", "Cs"}
            for char in item
        ):
            raise SearchPolicyDenied("Query value is invalid")
    normalized_query = urlencode(query, doseq=True, quote_via=quote, safe="-._~")
    return urlunsplit(
        (
            parts.scheme.lower(),
            host + (f":{port}" if port and port != standard_port else ""),
            path,
            normalized_query,
            "",
        )
    )
