"""Read-only access to the CP850/CP950 web interface for the Dolby Atmos state.

The web UI (GWT app under /prototype/) polls
    GET /prototype/TroutMockData?get_updates      (HTTP Basic auth)
and receives a JSON list of change events. While Dolby Atmos is rendered the
processor keeps sending

    {"id": "AtmosContentEvent_1015", "status": 1, "reversionReason": 0,
     "uuid": "...", "frameRate": 24, "frameNumber": 257, "encrypted": false, ...}

When Atmos stops it sends one event with "status": 2 (uuid "n/a"), then
nothing more. Observed on a CP850; the CP950A uses the same interface.
This client only ever sends GET requests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

from .common import ProcessorAuthError, ProcessorError

_LOGGER = logging.getLogger(__name__)

UPDATES_PATH = "/prototype/TroutMockData?get_updates"
STATUS_PATH = "/prototype/TroutMockData?product_status"
HTTP_TIMEOUT = 8.0

ATMOS_EVENT = "AtmosContentEvent"
ATMOS_PLAYING = 1
ATMOS_STALE = 20.0  # no Atmos event for this long while "playing" -> not playing


class AtmosTracker:
    """Turns the event stream into 'Atmos is rendered right now'."""

    def __init__(self) -> None:
        self.last: dict[str, Any] | None = None
        self.last_at: float | None = None

    def feed(self, events: list[Any], now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for event in events:
            if isinstance(event, dict) and str(event.get("id", "")).startswith(ATMOS_EVENT):
                self.last = event
                self.last_at = now

    def active(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.last is None or self.last_at is None:
            return False
        if self.last.get("status") != ATMOS_PLAYING:
            return False
        return now - self.last_at <= ATMOS_STALE

    def attributes(self) -> dict[str, Any]:
        e = self.last or {}
        return {
            "status_code": e.get("status"),
            "reversion_reason": e.get("reversionReason"),
            "frame_rate": e.get("frameRate") or None,
            "uuid": e.get("uuid") if e.get("uuid") not in (None, "n/a") else None,
            "encrypted": e.get("encrypted"),
        }


class WebClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
        port: int = 80,
    ) -> None:
        self.base = f"http://{host}" if port == 80 else f"http://{host}:{port}"
        self._auth = aiohttp.BasicAuth(username, password or "")
        self._session = session
        self._own_session = session is None
        self._started = False

    async def _get(self, path: str) -> Any:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.get(
                self.base + path,
                auth=self._auth,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            ) as resp:
                if resp.status in (401, 403):
                    raise ProcessorAuthError("Weboberfläche: Anmeldung abgelehnt")
                if resp.status != 200:
                    raise ProcessorError(f"Weboberfläche: HTTP {resp.status}")
                text = await resp.text(errors="replace")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ProcessorError(f"Weboberfläche nicht erreichbar: {err}") from err
        if not text.strip():
            return []
        try:
            return json.loads(text)
        except ValueError:
            _LOGGER.debug("%s: no JSON from %s: %.200s", self.base, path, text)
            return []

    async def poll(self) -> list[Any]:
        """Return the events since the last poll."""
        if not self._started:
            # The web UI reads the full state once before polling for updates.
            status = await self._get(STATUS_PATH)
            _LOGGER.debug("%s product_status: %.2000s", self.base, json.dumps(status))
            self._started = True
        events = await self._get(UPDATES_PATH)
        return events if isinstance(events, list) else [events]

    async def close(self) -> None:
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None
