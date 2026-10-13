"""Minimal aiohttp stand-in for the offline tests (only what web_client.py uses).

Home Assistant ships the real aiohttp; this file is only put on sys.path by
tests/fake_dolby.py when aiohttp is not installed.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import http.client
from urllib.parse import urlsplit


class ClientError(Exception):
    pass


@dataclass
class BasicAuth:
    login: str
    password: str = ""

    def encode(self) -> str:
        raw = f"{self.login}:{self.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()


@dataclass
class ClientTimeout:
    total: float | None = None


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    async def text(self, errors: str = "strict") -> str:
        return self._body.decode("utf-8", errors=errors)

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class _Request:
    def __init__(self, url: str, auth: BasicAuth | None, timeout: ClientTimeout | None) -> None:
        self.url, self.auth, self.timeout = url, auth, timeout

    def _fetch(self) -> _Response:
        parts = urlsplit(self.url)
        conn = http.client.HTTPConnection(
            parts.hostname, parts.port or 80, timeout=(self.timeout.total if self.timeout else 10)
        )
        headers = {"Authorization": self.auth.encode()} if self.auth else {}
        try:
            conn.request("GET", parts.path + ("?" + parts.query if parts.query else ""), headers=headers)
            resp = conn.getresponse()
            return _Response(resp.status, resp.read())
        except OSError as err:
            raise ClientError(str(err)) from err
        finally:
            conn.close()

    async def __aenter__(self) -> _Response:
        return await asyncio.get_running_loop().run_in_executor(None, self._fetch)

    async def __aexit__(self, *exc) -> None:
        return None


class ClientSession:
    def get(self, url: str, auth: BasicAuth | None = None, timeout: ClientTimeout | None = None):
        return _Request(url, auth, timeout)

    async def close(self) -> None:
        return None
