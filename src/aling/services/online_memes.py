"""Policy-gated static image lookup with an ephemeral, in-memory resource store."""

from __future__ import annotations

import asyncio
import secrets
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import httpx

from aling import __version__
from aling.services.http import read_limited
from aling.services.memes import ValidatedImage, validate_image_bytes
from aling.services.search_budget import SearchAdmission, SearchQuotaLimits, TaskBudget
from aling.services.web import public_ip, resolve_public_candidate

MAX_ONLINE_IMAGE_BYTES = 5_000_000
MAX_ONLINE_IMAGE_PIXELS = 12_000_000
_MAX_ITEMS = 20
_MAX_CACHE_BYTES = 50_000_000
_TTL_SECONDS = 300


@dataclass(frozen=True)
class TemporaryMemeRef:
    opaque_id: str
    description: str
    source_id: str
    mode: str = "online"

    @property
    def resource_id(self) -> str:
        return self.opaque_id


@dataclass(frozen=True)
class _TemporaryMeme:
    group_id: int
    user_id: int
    task_id: str
    source_id: str
    policy_revision: tuple[int, int]
    feature_revision: tuple[int, int]
    content: bytes
    mime_type: str
    expires_at: float
    task_context: Any
    result_id: str


class TemporaryMemeStore:
    """Small process-memory cache. Reservations count while downloads are in flight."""

    def __init__(
        self,
        *,
        max_items: int = _MAX_ITEMS,
        max_bytes: int = _MAX_CACHE_BYTES,
        ttl_seconds: int = _TTL_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.max_items = min(max_items, _MAX_ITEMS)
        self.max_bytes = min(max_bytes, _MAX_CACHE_BYTES)
        self.ttl_seconds = min(ttl_seconds, _TTL_SECONDS)
        self.clock = clock
        self._items: OrderedDict[str, _TemporaryMeme] = OrderedDict()
        self._reservations: dict[str, int] = {}

    async def reserve(self, max_bytes: int = MAX_ONLINE_IMAGE_BYTES) -> str:
        await self.expire()
        if (
            type(max_bytes) is not int
            or max_bytes <= 0
            or max_bytes > MAX_ONLINE_IMAGE_BYTES
            or len(self._items) + len(self._reservations) >= self.max_items
            or self._used_bytes() + sum(self._reservations.values()) + max_bytes > self.max_bytes
        ):
            raise RuntimeError("Temporary meme capacity is full")
        token = secrets.token_urlsafe(16)
        self._reservations[token] = max_bytes
        return token

    def release(self, reservation: str) -> None:
        self._reservations.pop(reservation, None)

    async def put(
        self,
        group_id: int,
        user_id: int,
        task_id: str,
        source_id: str,
        policy_revision: tuple[int, int],
        image_bytes: bytes,
        expires_at: float | None = None,
        *,
        feature_revision: tuple[int, int] = (0, 0),
        reservation: str | None = None,
        task_context: Any = None,
        result_id: str = "",
        description: str = "在线表情包",
    ) -> TemporaryMemeRef:
        await self.expire()
        reserved = self._reservations.get(reservation) if reservation else None
        if reservation is not None and reserved is None:
            raise RuntimeError("Temporary meme reservation expired")
        validated = _validate_static_image(image_bytes)
        if reserved is None:
            reservation = await self.reserve(len(validated.content))
            reserved = self._reservations[reservation]
        if (
            len(validated.content) > reserved
            or len(self._items) + len(self._reservations) > self.max_items
            or self._used_bytes() + sum(self._reservations.values()) > self.max_bytes
        ):
            self.release(reservation)
            raise RuntimeError("Temporary meme capacity is full")
        self.release(reservation)
        opaque_id = secrets.token_urlsafe(16)
        while opaque_id in self._items:
            opaque_id = secrets.token_urlsafe(16)
        expiry = min(expires_at or self.clock() + self.ttl_seconds, self.clock() + self.ttl_seconds)
        self._items[opaque_id] = _TemporaryMeme(
            group_id,
            user_id,
            task_id,
            source_id,
            policy_revision,
            feature_revision,
            validated.content,
            validated.mime_type,
            expiry,
            task_context,
            result_id,
        )
        return TemporaryMemeRef(opaque_id, description[:200], source_id)

    async def read_for_send(
        self,
        opaque_id: str,
        trusted_group_id: int,
        trusted_user_id: int,
        current_policy: Callable[[_TemporaryMeme], Awaitable[bool]],
        *,
        expected_task_id: str | None = None,
    ) -> ValidatedImage:
        await self.expire()
        item = self._items.get(opaque_id)
        if (
            item is None
            or item.group_id != trusted_group_id
            or item.user_id != trusted_user_id
            or (expected_task_id is not None and item.task_id != expected_task_id)
            or item.expires_at <= self.clock()
        ):
            self._items.pop(opaque_id, None)
            raise PermissionError("Temporary meme is unavailable")
        try:
            valid = await current_policy(item)
        except Exception:
            self._items.pop(opaque_id, None)
            raise PermissionError("Temporary meme authorization expired") from None
        if not valid:
            self._items.pop(opaque_id, None)
            raise PermissionError("Temporary meme authorization expired")
        return ValidatedImage(item.content, item.mime_type)

    async def revoke_policy(
        self,
        group_id: int,
        current_revision: tuple[int, int],
        allowed_source_ids: set[str],
        *,
        current_feature_revision: tuple[int, int] | None = None,
    ) -> int:
        await self.expire()
        doomed = [
            key
            for key, item in self._items.items()
            if item.group_id == group_id
            and (
                item.policy_revision != current_revision
                or item.source_id not in allowed_source_ids
                or (
                    current_feature_revision is not None
                    and item.feature_revision != current_feature_revision
                )
            )
        ]
        for key in doomed:
            self._items.pop(key, None)
        return len(doomed)

    async def expire(self) -> int:
        now = self.clock()
        doomed = [key for key, item in self._items.items() if item.expires_at <= now]
        for key in doomed:
            self._items.pop(key, None)
        return len(doomed)

    async def contains(self, opaque_id: str) -> bool:
        await self.expire()
        return opaque_id in self._items

    async def size(self) -> tuple[int, int]:
        await self.expire()
        return len(self._items), self._used_bytes()

    async def group_ids(self) -> set[int]:
        await self.expire()
        return {item.group_id for item in self._items.values()}

    async def clear(self) -> None:
        self._items.clear()
        self._reservations.clear()

    def _used_bytes(self) -> int:
        return sum(len(item.content) for item in self._items.values())


class MemeImageClient:
    """Fetch one public image with pinned DNS, standard ports, and bounded bytes."""

    def __init__(
        self,
        *,
        timeout: float = 10,
        max_bytes: int = MAX_ONLINE_IMAGE_BYTES,
        max_pixels: int = MAX_ONLINE_IMAGE_PIXELS,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable = resolve_public_candidate,
    ) -> None:
        self.timeout = min(timeout, 10)
        self.max_bytes = min(max_bytes, MAX_ONLINE_IMAGE_BYTES)
        self.max_pixels = min(max_pixels, MAX_ONLINE_IMAGE_PIXELS)
        self._transport, self.resolver = transport, resolver

    async def fetch(self, value: str, *, budget: TaskBudget | None = None) -> ValidatedImage:
        if not isinstance(value, str) or len(value) > 2048 or "\\" in value:
            raise ValueError("Unsafe image URL")
        try:
            parts = urlsplit(value)
            url = httpx.URL(value)
            standard_port = 443 if url.scheme == "https" else 80
            if (
                url.scheme not in {"http", "https"}
                or not url.host
                or parts.username is not None
                or parts.password is not None
                or parts.fragment
                or (url.port is not None and url.port != standard_port)
                or any(ord(char) < 33 for char in value)
            ):
                raise ValueError("Unsafe image URL")
        except (ValueError, httpx.InvalidURL) as exc:
            raise ValueError("Unsafe image URL") from exc
        timeout = self.timeout
        if budget is not None:
            timeout = min(
                timeout, budget.limits.request_timeout_seconds, budget.remaining_work_seconds()
            )
            if timeout <= 0:
                raise TimeoutError("Online meme work deadline reached")
            budget.reserve_external_operation("image")
        port = url.port or standard_port
        async with asyncio.timeout(timeout):
            addresses = await self.resolver(url.host, port)
            if not addresses or not all(public_ip(address) for address in addresses):
                raise ValueError("Image destination is not exclusively public")
            pinned = url.copy_with(host=addresses[0])
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                trust_env=False,
                follow_redirects=False,
                transport=self._transport,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            ) as client:
                async with client.stream(
                    "GET",
                    pinned,
                    headers={
                        "Host": url.netloc.decode("ascii"),
                        "User-Agent": f"ALING/{__version__}",
                        "Accept": "image/png,image/jpeg,image/webp",
                        "Accept-Encoding": "identity",
                    },
                    extensions={"sni_hostname": url.host},
                ) as response:
                    if response.is_redirect:
                        raise ValueError("Image redirects are disabled")
                    response.raise_for_status()
                    content = await read_limited(response, self.max_bytes)
                    image = _validate_static_image(content, self.max_bytes, self.max_pixels)
                    declared = (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    )
                    if declared != image.mime_type:
                        raise ValueError("Image content type does not match its data")
                    return image

    async def aclose(self) -> None:
        if self._transport is not None:
            await self._transport.aclose()


class OnlineMemeService:
    def __init__(
        self,
        local_memes,
        policy,
        search,
        image_client: MemeImageClient,
        *,
        store: TemporaryMemeStore | None = None,
        admission: SearchAdmission | None = None,
        page_source_validator: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self.local_memes, self.policy, self.search, self.image_client = (
            local_memes,
            policy,
            search,
            image_client,
        )
        self.store = store or TemporaryMemeStore()
        self.admission = admission or SearchAdmission()
        self.page_source_validator = page_source_validator
        self._tasks: set[asyncio.Task] = set()

    async def search_and_download(
        self, context, query: str, mode: str, budget: TaskBudget
    ) -> TemporaryMemeRef | Any | None:
        if mode not in {"local", "auto", "online"}:
            raise ValueError("Unknown meme mode")
        if mode in {"local", "auto"}:
            local = await self.local_memes.search(query, limit=1)
            if local:
                return local[0]
            if mode == "local":
                return None
        current = asyncio.current_task()
        if current is not None:
            self._tasks.add(current)
        try:
            return await self._online(context, query, budget)
        finally:
            if current is not None:
                self._tasks.discard(current)

    async def _online(self, context, query: str, budget: TaskBudget) -> TemporaryMemeRef | None:
        from aling.services.search_policy import SearchPolicyDenied, SearchPurpose

        feature_revision = self._feature_revision(context.group_id)
        authorized = await self.policy.authorize_online(
            context, query, "online_meme", budget=budget
        )
        deployment = await self.policy.store.get_deployment_search_policy()
        group = await self.policy.store.get_group_search_policy(context.group_id)
        purpose = authorized.purpose or SearchPurpose.ORDINARY_MEME
        page_sources = [
            source
            for source in deployment.sources
            if source.kind == "page"
            and source.purpose == purpose.value
            and (group.source_ids is None or source.source_id in group.source_ids)
        ]
        if not page_sources:
            raise SearchPolicyDenied("No approved result-page source remains")
        limits = deployment.limits
        effective = group.limits
        if effective is not None:
            from dataclasses import fields

            from aling.core.storage import SearchLimits

            limits = SearchLimits(
                **{
                    field.name: min(getattr(limits, field.name), getattr(effective, field.name))
                    for field in fields(limits)
                }
            )
        budget.apply_policy_limits(limits)
        quota_limits = SearchQuotaLimits.from_policy(limits)
        async with self.admission.slot(
            group_id=context.group_id,
            user_id=context.user_id,
            limits=quota_limits,
        ):
            hits = await self.search.search(
                authorized.normalized_query or query,
                category="images",
                language="auto",
                safe_search=2,
                limit=min(3, budget.limits.max_results),
                budget=budget,
            )
            return await self._fetch_approved_image(
                context, authorized, purpose, page_sources, hits, budget, feature_revision
            )

    async def _fetch_approved_image(
        self, context, authorized, purpose, page_sources, hits, budget, feature_revision
    ) -> TemporaryMemeRef | None:
        selected = None
        for hit in hits:
            if (
                not hit.image_url
                or not _standard_public_url(hit.url)
                or not _standard_public_url(hit.image_url)
            ):
                continue
            valid_page = False
            for page_source in page_sources:
                try:
                    canonical_page = await self.policy.validate_result_url(
                        hit.url, purpose, page_source.source_id
                    )
                    if canonical_page and (
                        self.page_source_validator is None
                        or await self.page_source_validator(canonical_page)
                    ):
                        valid_page = True
                        break
                except Exception:
                    continue
            if not valid_page:
                continue
            for source_id in authorized.source_ids:
                try:
                    image_url = await self.policy.validate_result_url(
                        hit.image_url, purpose, source_id
                    )
                    result_id = await self.policy.register_result(authorized, image_url, source_id)
                    # Resolve through the issued search-result handle; callers never send a raw URL.
                    image_url = await self.policy.resolve_result(
                        authorized, result_id, operation="image"
                    )
                except Exception:
                    continue
                selected = (hit, source_id, result_id, image_url)
                break
            if selected is not None:
                break
        if selected is None:
            return None
        hit, source_id, result_id, image_url = selected
        reservation = await self.store.reserve(self.image_client.max_bytes)
        try:
            image = await self.image_client.fetch(image_url, budget=budget)
            await self.policy.revalidate(authorized, "online_meme", source_id)
            await self.policy.revalidate(authorized, "image", result_id)
            await self.policy.resolve_result(authorized, result_id, operation="image")
            if self._feature_revision(context.group_id) != feature_revision:
                raise PermissionError("Online meme feature policy changed")
            return await self.store.put(
                context.group_id,
                context.user_id,
                context.task_id,
                source_id,
                context.policy_revision,
                image.content,
                self.store.clock() + self.store.ttl_seconds,
                feature_revision=feature_revision,
                reservation=reservation,
                task_context=authorized,
                result_id=result_id,
                description=hit.title or "在线表情包",
            )
        finally:
            self.store.release(reservation)

    async def read_for_send(
        self, opaque_id: str, group_id: int, user_id: int, *, task_id: str
    ) -> ValidatedImage:
        async def recheck(item: _TemporaryMeme) -> bool:
            if item.task_context is None or not item.result_id:
                return False
            if self._feature_revision(group_id) != item.feature_revision:
                return False
            await self.policy.revalidate(item.task_context, "online_meme", item.source_id)
            await self.policy.revalidate(item.task_context, "image", item.result_id)
            url = await self.policy.resolve_result(
                item.task_context, item.result_id, operation="image"
            )
            return bool(url) and self._feature_revision(group_id) == item.feature_revision

        return await self.store.read_for_send(
            opaque_id, group_id, user_id, recheck, expected_task_id=task_id
        )

    async def aclose(self) -> None:
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.store.clear()
        await self.image_client.aclose()

    async def expire(self) -> int:
        removed = await self.store.expire()
        for group_id in await self.store.group_ids():
            deployment = await self.policy.store.get_deployment_search_policy()
            group = await self.policy.store.get_group_search_policy(group_id)
            allowed = {
                source.source_id
                for source in deployment.sources
                if group.source_ids is None or source.source_id in group.source_ids
            }
            removed += await self.store.revoke_policy(
                group_id,
                (deployment.revision, group.revision),
                allowed,
                current_feature_revision=self._feature_revision(group_id),
            )
        return removed

    def _feature_revision(self, group_id: int) -> tuple[int, int]:
        features = getattr(self.policy, "features", None)
        revision = getattr(features, "online_meme_revision", None)
        return revision(group_id) if callable(revision) else (0, 0)


def _validate_static_image(
    content: bytes,
    max_bytes: int = MAX_ONLINE_IMAGE_BYTES,
    max_pixels: int = MAX_ONLINE_IMAGE_PIXELS,
) -> ValidatedImage:
    image = validate_image_bytes(content, max_bytes=max_bytes, max_pixels=max_pixels)
    if image.mime_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError("Only PNG, JPEG and static WebP images are supported")
    return image


def _standard_public_url(value: str) -> bool:
    try:
        url = httpx.URL(value)
        port = url.port or (443 if url.scheme == "https" else 80)
    except (ValueError, httpx.InvalidURL):
        return False
    return (
        url.scheme in {"http", "https"}
        and bool(url.host)
        and port == (443 if url.scheme == "https" else 80)
        and not url.username
        and not url.password
        and not url.fragment
    )
