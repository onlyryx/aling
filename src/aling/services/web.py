import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from aling.core.extensions import WebPage
from aling.services.http import read_limited


class UnsafeURL(ValueError):
    """The destination cannot be safely requested."""


async def resolve_public_candidate(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return list(dict.fromkeys(info[4][0] for info in infos))


def public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        # Refuse address translation/tunnelling and zone IDs, even if marked global.
        if address.ipv4_mapped or address.sixtofour or address.teredo or address.scope_id:
            return False
        if address in ipaddress.ip_network("64:ff9b::/96"):
            return False
    return address.is_global and not (
        address.is_multicast or address.is_reserved or address.is_unspecified
    )


class SafeWebClient:
    """Public HTTP(S) only; no redirects, proxies, cookies or second DNS lookup."""

    def __init__(
        self,
        timeout: float = 10,
        max_bytes: int = 1_000_000,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str, int], Awaitable[list[str]]] = resolve_public_candidate,
    ) -> None:
        self.timeout, self.max_bytes, self.resolver = timeout, max_bytes, resolver
        self._transport = transport

    async def fetch(self, value: str) -> str:
        if len(value) > 2048 or "\\" in value or any(ord(char) < 33 for char in value):
            raise UnsafeURL("Malformed URL")
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
            ):
                raise UnsafeURL("Only standard HTTP(S) URLs without credentials are supported")
        except (ValueError, httpx.InvalidURL) as exc:
            raise UnsafeURL("Malformed URL") from exc

        async with asyncio.timeout(self.timeout):
            try:
                addresses = await self.resolver(url.host, standard_port)
                if not addresses or not all(public_ip(address) for address in addresses):
                    raise UnsafeURL("Destination is not exclusively public")
            except (OSError, ValueError) as exc:
                raise UnsafeURL("Destination resolution rejected") from exc
            pinned = url.copy_with(host=addresses[0])
            # TLS verifies the original hostname. The TCP destination is the validated IP.
            # See HTTPCore's documented sni_hostname request extension.
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                trust_env=False,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "GET",
                    pinned,
                    headers={
                        "Host": url.netloc.decode("ascii"),
                        "User-Agent": "ALING/0.1.0",
                        "Accept-Encoding": "identity",
                    },
                    extensions={"sni_hostname": url.host},
                ) as response:
                    if response.is_redirect:
                        raise UnsafeURL("Redirects are disabled")
                    response.raise_for_status()
                    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if mime not in {"text/html", "text/plain", "application/xhtml+xml"}:
                        raise ValueError("Unsupported content type")
                    return (await read_limited(response, self.max_bytes)).decode(
                        response.encoding or "utf-8", errors="replace"
                    )

    async def parse(self, url: str) -> WebPage:
        soup = BeautifulSoup(await self.fetch(url), "html.parser")
        title = soup.title.get_text(" ", strip=True)[:200] if soup.title else ""
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        return WebPage(url=url, title=title, text=soup.get_text(" ", strip=True)[:10000])

    async def aclose(self) -> None:
        if self._transport:
            await self._transport.aclose()
