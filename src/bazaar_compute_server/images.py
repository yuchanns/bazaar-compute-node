"""Pictures from outside, brought to the page by the server: whoever put one
in a chat sees the server come for it, never the person looking."""

from __future__ import annotations

import hmac
import socket
from collections.abc import AsyncIterator
from hashlib import sha256
from ipaddress import ip_address
from urllib.parse import urlencode, urljoin, urlsplit

from aiohttp import (
    ClientError,
    ClientSession,
    ClientTimeout,
    DefaultResolver,
    TCPConnector,
)
from aiohttp.abc import AbstractResolver, ResolveResult
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from .sessions import Sessions

PATH = "/images"
# a picture in a chat, not a download: what a phone would post
MAX_BYTES = 10 * 1024 * 1024
_CHUNK = 64 * 1024
_TIMEOUT = ClientTimeout(total=20)
_REDIRECTS = 3
_REDIRECTED = (301, 302, 303, 307, 308)


class Images:
    """The page holds a signed address for each picture, so only a picture
    the page itself showed can be asked for through the server."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions
        self._client: ClientSession | None = None

    def address(self, url: str) -> str | None:
        """Where the page asks for a picture; nothing for one that is not on
        the web."""

        if not url.startswith(("http://", "https://")):
            return None
        return f"{PATH}?{urlencode({'u': url, 's': self._sign(url)})}"

    async def fetch(self, request: Request) -> Response:
        url = request.query_params.get("u", "")
        if not hmac.compare_digest(request.query_params.get("s", ""), self._sign(url)):
            return Response(status_code=403)
        if self._client is None:
            self._client = ClientSession(
                connector=TCPConnector(resolver=_Public()), timeout=_TIMEOUT
            )
        try:
            # each address on the way is looked at: an address given as a
            # number goes to the network without a name to resolve
            for _ in range(_REDIRECTS + 1):
                if not _on_the_web(url):
                    return Response(status_code=502)
                upstream = await self._client.get(
                    url, headers={"User-Agent": "bcs"}, allow_redirects=False
                )
                onward = upstream.headers.get("Location")
                if upstream.status not in _REDIRECTED or onward is None:
                    break
                upstream.release()
                url = urljoin(url, onward)
            else:
                return Response(status_code=502)
        except ClientError, TimeoutError:
            return Response(status_code=502)
        if not (
            upstream.ok
            and upstream.content_type.startswith("image/")
            and (upstream.content_length or 0) <= MAX_BYTES
        ):
            upstream.release()
            return Response(status_code=502)

        async def body() -> AsyncIterator[bytes]:
            sent = 0
            try:
                async for chunk in upstream.content.iter_chunked(_CHUNK):
                    sent += len(chunk)
                    if sent > MAX_BYTES:
                        break
                    yield chunk
            finally:
                upstream.release()

        return StreamingResponse(
            body(),
            media_type=upstream.content_type,
            # opened as a page of its own, a picture with scripts in it - an
            # SVG - runs none, and is no part of this site
            headers={
                "Cache-Control": "private, max-age=86400",
                "Content-Security-Policy": "sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    def _sign(self, url: str) -> str:
        # the session key, under its own label so a signature is never a cookie
        return hmac.new(
            self._sessions.key, b"image:" + url.encode(), sha256
        ).hexdigest()


class _Public(AbstractResolver):
    """Names resolve to public addresses only: a picture said to live on the
    server's own network is not fetched, whatever the name says."""

    def __init__(self) -> None:
        self._resolver = DefaultResolver()

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        found = await self._resolver.resolve(host, port, family)
        public = [item for item in found if reachable(item["host"])]
        if not public:
            raise OSError(f"{host} is not on the public internet")
        return public

    async def close(self) -> None:
        await self._resolver.close()


def reachable(host: str) -> bool:
    return ip_address(host).is_global


def _on_the_web(url: str) -> bool:
    """Whether the address is one the server may go to: on the web, and not
    given as a private address in numbers, which no name would be asked for."""

    if not url.startswith(("http://", "https://")):
        return False
    host = urlsplit(url).hostname or ""
    try:
        ip_address(host)
    except ValueError:
        return True
    return reachable(host)


__all__ = ["Images", "reachable"]
