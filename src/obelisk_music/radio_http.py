"""Radio-only HTTP: public DNS pinning and validated, bounded redirects."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx


class RadioError(ValueError):
    """A safe, actionable radio failure; never include remote response bodies."""


class RadioNetworkError(RadioError):
    """Transient radio network failure, eligible for a bounded read retry."""


def public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        return (
            address.is_global
            and not address.is_multicast
            and not (
                isinstance(address, ipaddress.IPv6Address)
                and (address.ipv4_mapped or address.sixtofour or address.teredo)
            )
        )
    except ValueError:
        return False


def validate_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if (
            len(value) > 4096
            or any(ord(c) < 33 or ord(c) == 127 for c in value)
            or parts.scheme not in ("http", "https")
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or "\\" in value
            or "%" in host
            or host.rstrip(".").lower() == "localhost"
            or host.rstrip(".").lower().endswith((".localhost", ".local", ".internal"))
            or parts.port == 0
        ):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host.encode("idna")
        else:
            if not public_ip(host):
                raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise RadioError(
            "Use a public HTTP(S) stream URL without credentials or fragments"
        ) from None


async def resolve_public(host: str, port: int) -> str:
    try:
        records = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM),
            5,
        )
    except (OSError, TimeoutError):
        raise RadioNetworkError(
            "Radio host could not be resolved; check your connection"
        ) from None
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or not all(public_ip(address) for address in addresses):
        raise RadioError(
            "Radio URL resolves to a private or unsafe network destination"
        )
    return addresses[0]


class PublicTransport(httpx.AsyncBaseTransport):
    """Connect to the checked IP, retaining the original TLS identity and Host."""

    def __init__(self, *, transport=None, resolver=resolve_public):
        self.transport = transport or httpx.AsyncHTTPTransport(
            retries=0, trust_env=False, limits=httpx.Limits(max_keepalive_connections=0)
        )
        self.resolver = resolver

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        validate_url(str(request.url))
        host = request.url.host
        address = await self.resolver(
            host, request.url.port or (443 if request.url.scheme == "https" else 80)
        )
        if not public_ip(address):
            raise RadioError("Radio resolver returned an unsafe address")
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=address),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return await self.transport.handle_async_request(pinned)

    async def aclose(self) -> None:
        await self.transport.aclose()


def radio_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=PublicTransport(),
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(15, connect=8),
        headers={"User-Agent": "ObeliskPlayer/Radio", "Accept-Encoding": "identity"},
    )


@asynccontextmanager
async def open_stream(client: httpx.AsyncClient, url: str):
    for _ in range(6):
        validate_url(url)
        async with client.stream(
            "GET", url, headers={"Icy-MetaData": "1"}, follow_redirects=False
        ) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if not location or len(location) > 4096:
                    raise RadioError("Station returned an invalid redirect")
                url = str(response.url.join(location))
                continue
            if response.status_code != 200:
                raise RadioError(
                    f"Station rejected playback (HTTP {response.status_code}); try another station"
                )
            yield response
            return
    raise RadioError("Station redirected too many times; use a direct audio URL")
