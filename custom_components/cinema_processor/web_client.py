"""Read-only access to the CP850/CP950 web interface for the Dolby Atmos state.

The web UI (GWT app under /prototype/) logs in, then polls
    GET /prototype/TroutMockData?get_updates      (headers SessionId, ClientId)
and receives a JSON list of change events. While Dolby Atmos is rendered the
processor keeps sending

    {"id": "AtmosContentEvent_1015", "status": 1, "reversionReason": 0,
     "uuid": "...", "frameRate": 24, "frameNumber": 257, "encrypted": false, ...}

When Atmos stops it sends one event with "status": 2 (uuid "n/a"), then
nothing more. Observed on a CP850; the CP950A uses the same interface.

"ignore Dolby Atmos" button of the web UI (captured on a CP850):
    POST /prototype/TroutMockData?send_message
    [{"id":"ForcePcmControlEvent_1066", "forcePCM":true}]    (false = back)
The processor confirms with the same event in get_updates. It reverts to
Atmos by itself on reboot or macro change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .common import ProcessorAuthError, ProcessorError

_LOGGER = logging.getLogger(__name__)

UPDATES_PATH = "/prototype/TroutMockData?get_updates"
LOGIN_PATH = "/prototype/TroutMockData?login"
SEND_PATH = "/prototype/TroutMockData?send_message"
FORCE_PCM_EVENT = "ForcePcmControlEvent"
FORCE_PCM_ID = "ForcePcmControlEvent_1066"
MACRO_CONFIG_ID = "MacroConfigurationEvent_1032"


def button_macros(events: list[Any]) -> dict[int, str] | None:
    """Macros on the front-panel buttons from MacroConfigurationEvent entries.

    {button number: name}; the button number is what sys.macro_preset uses.
    None if the events contain no macro configuration at all.
    """
    found = False
    macros: dict[int, str] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        if not str(event.get("id", "")).startswith("MacroConfigurationEvent"):
            continue
        found = True
        button = event.get("buttonAssignment") or 0
        name = str(event.get("name") or "").strip()
        if event.get("defined") and isinstance(button, int) and button > 0 and name:
            macros[button] = name
    return dict(sorted(macros.items())) if found else None
_SESSION_RE = re.compile(r"sessionId=([^;\s]*)")
HTTP_TIMEOUT = 8.0

ATMOS_EVENT = "AtmosContentEvent"
ATMOS_PLAYING = 1
ATMOS_STALE = 20.0  # no Atmos event for this long while "playing" -> not playing


class AtmosTracker:
    """Turns the event stream into 'Atmos is rendered right now' and 'PCM forced'."""

    def __init__(self) -> None:
        self.last: dict[str, Any] | None = None
        self.last_at: float | None = None
        self.force_pcm: bool | None = None  # unknown until the processor tells us
        self.changed_at: datetime | None = None  # last Atmos on/off change
        self._was_active: bool | None = None

    def feed(self, events: list[Any], now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id", ""))
            if event_id.startswith(ATMOS_EVENT):
                self.last = event
                self.last_at = now
                if event.get("status") == ATMOS_PLAYING:
                    self.force_pcm = False  # Atmos renders, so PCM is not forced
            elif event_id.startswith(FORCE_PCM_EVENT) and "forcePCM" in event:
                self.force_pcm = bool(event["forcePCM"])
        active = self.active(now)
        if self._was_active is not None and active != self._was_active:
            self.changed_at = datetime.now(timezone.utc)
        self._was_active = active

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
            "pcm_forced": self.force_pcm,
            "last_change": self.changed_at.isoformat(timespec="milliseconds")
            if self.changed_at
            else None,
        }


def multipart_form(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = "----CinemaProcessor" + "".join(random.choices("0123456789abcdef", k=16))
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in fields.items()
    ]
    parts.append(f"--{boundary}--\r\n")
    return "".join(parts).encode("utf-8"), f"multipart/form-data; boundary={boundary}"


class _SessionExpired(Exception):
    pass


class WebClient:
    """Logs in like the web UI and polls the update feed.

    Login (from the web UI code): multipart form POST to
    /prototype/TroutMockData?login with the fields clientId, userName and
    password. The answer text contains "sessionId=<id>;" (and
    "loggedInUserLevel=<n>;"); every later request carries the headers
    "SessionId: <id>" and "ClientId: <number>". No HTTP Basic auth.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
        port: int = 80,
    ) -> None:
        self.base = f"http://{host}" if port == 80 else f"http://{host}:{port}"
        self._username = username
        self._password = password or ""
        self._session = session
        self._own_session = session is None
        self._client_id = str(random.randint(100000000, 999999999))
        self._session_id: str | None = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def login(self) -> None:
        # Built by hand so it looks exactly like the browser's form post
        # (aiohttp.FormData would send plain text fields url-encoded).
        body, content_type = multipart_form(
            {"clientId": self._client_id, "userName": self._username, "password": self._password}
        )
        try:
            async with self._http().post(
                self.base + LOGIN_PATH,
                data=body,
                headers={"Content-Type": content_type},
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            ) as resp:
                text = await resp.text(errors="replace")
                status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ProcessorError(f"Weboberfläche nicht erreichbar: {err}") from err
        _LOGGER.debug("%s login -> HTTP %s: %.300r", self.base, status, text)
        m = _SESSION_RE.search(text)
        if m and m.group(1):
            self._session_id = m.group(1)
            _LOGGER.debug("%s: logged in to the web interface", self.base)
            return
        self._session_id = None
        if status == 200 or status in (401, 403):
            raise ProcessorAuthError("Weboberfläche: Benutzer oder Passwort falsch")
        raise ProcessorError(f"Weboberfläche: Anmeldung HTTP {status}")

    async def _get(self, path: str) -> Any:
        headers = {"ClientId": self._client_id}
        if self._session_id:
            headers["SessionId"] = self._session_id
        try:
            async with self._http().get(
                self.base + path,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            ) as resp:
                if resp.status in (401, 403):
                    raise _SessionExpired
                if resp.status != 200:
                    raise ProcessorError(f"Weboberfläche: {path} -> HTTP {resp.status}")
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

    async def poll(self, required: list[str] | None = None) -> list[Any]:
        """Return the events since the last poll (logs in when needed).

        required: event ids whose full current state the processor should send
        along (the web UI does this with "&required=..." on start-up).
        """
        path = UPDATES_PATH
        if required:
            path += "&required=" + ",".join(required)
        if self._session_id is None:
            await self.login()
        try:
            events = await self._get(path)
        except _SessionExpired:
            await self.login()  # session timed out or processor rebooted
            try:
                events = await self._get(path)
            except _SessionExpired:
                self._session_id = None
                raise ProcessorAuthError("Weboberfläche: Sitzung abgelehnt") from None
        return events if isinstance(events, list) else [events]

    async def _post(self, path: str, body: str) -> None:
        headers = {"ClientId": self._client_id, "Content-Type": "text/plain; charset=utf-8"}
        if self._session_id:
            headers["SessionId"] = self._session_id
        try:
            async with self._http().post(
                self.base + path,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            ) as resp:
                if resp.status in (401, 403):
                    raise _SessionExpired
                if resp.status != 200:
                    raise ProcessorError(f"Weboberfläche: {path} -> HTTP {resp.status}")
                await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ProcessorError(f"Weboberfläche nicht erreichbar: {err}") from err

    async def send_message(self, body: str) -> None:
        """Send a JSON event list exactly as the web UI does (logs in when needed)."""
        _LOGGER.debug("%s send_message %s", self.base, body)
        if self._session_id is None:
            await self.login()
        try:
            await self._post(SEND_PATH, body)
        except _SessionExpired:
            await self.login()
            try:
                await self._post(SEND_PATH, body)
            except _SessionExpired:
                self._session_id = None
                raise ProcessorAuthError("Weboberfläche: Sitzung abgelehnt") from None

    async def set_force_pcm(self, force: bool) -> None:
        # Same text as the web UI sends (including the space after the comma).
        await self.send_message(
            f'[{{"id":"{FORCE_PCM_ID}", "forcePCM":{"true" if force else "false"}}}]'
        )

    async def close(self) -> None:
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None
