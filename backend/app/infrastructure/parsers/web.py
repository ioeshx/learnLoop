"""Bounded single-page fetcher with SSRF-oriented URL validation."""

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

AddressResolver = Callable[[str], Awaitable[list[str]]]


@dataclass(frozen=True, slots=True)
class FetchedPage:
    content: bytes
    final_url: str
    media_type: str


class SafeWebPageFetcher:
    def __init__(
        self,
        *,
        max_bytes: int,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
        resolver: AddressResolver | None = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._resolver = resolver or _resolve_addresses

    async def fetch(self, url: str) -> FetchedPage:
        current_url = url.strip()
        for _ in range(4):
            await self._validate_url(current_url)
            async with self._client.stream(
                "GET",
                current_url,
                headers={
                    "User-Agent": "LearnLoop/0.1 (+local learning resource import)",
                    "Accept": "text/html,text/plain;q=0.9",
                },
                follow_redirects=False,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("web page redirect has no location")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                media_type = response.headers.get("content-type", "").partition(";")[
                    0
                ].strip().lower()
                if media_type not in {"text/html", "text/plain"}:
                    raise ValueError("URL must point to an HTML or plain-text page")
                declared_size = response.headers.get("content-length")
                if declared_size and int(declared_size) > self._max_bytes:
                    raise ValueError("web page exceeds the configured size limit")
                content = bytearray()
                async for block in response.aiter_bytes():
                    content.extend(block)
                    if len(content) > self._max_bytes:
                        raise ValueError("web page exceeds the configured size limit")
                if not content:
                    raise ValueError("web page is empty")
                return FetchedPage(
                    content=bytes(content),
                    final_url=str(response.url),
                    media_type=media_type,
                )
        raise ValueError("web page has too many redirects")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("web resource URL must use HTTP or HTTPS")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("web resource URL has an invalid host or credentials")
        if parsed.port not in {None, 80, 443}:
            raise ValueError("web resource URL must use port 80 or 443")
        addresses = await self._resolver(parsed.hostname)
        if not addresses:
            raise ValueError("web resource host did not resolve")
        for value in addresses:
            address = ipaddress.ip_address(value)
            if not address.is_global:
                raise ValueError("web resource URL resolves to a non-public address")


async def _resolve_addresses(hostname: str) -> list[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(hostname, None, type=0)
    return list({str(record[4][0]) for record in records})
