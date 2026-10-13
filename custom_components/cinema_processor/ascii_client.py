"""Async client for the Dolby ASCII control port (no Home Assistant imports).

Protocol (CP750 manual ch. 6.2, same family on CP850/CP950 without the
"cp750." prefix): one line per command, "<parameter> <value>" sets,
"<parameter> ?" queries, the processor answers "<parameter> <value>".
An unknown parameter is answered with an empty line, no error messages.

The CP850 is known to lock its control port after rapid successive TCP
connections, so this client keeps ONE persistent connection, paces its
commands and never reconnects in a tight loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from .common import ProcessorCommandError, ProcessorError, ProcessorTimeout

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
REPLY_TIMEOUT = 3.0  # wait for the answer to a query
SET_TIMEOUT = 2.0  # sets are normally echoed; a missing echo is not an error
BLANK_GRACE = 0.4  # after an empty line (unknown parameter) wait this long more
COMMAND_SPACING = 0.15  # minimum pause between two commands
CONNECT_SPACING = 3.0  # minimum pause between two connections to the same host
RECONNECT_BACKOFF = 10.0  # after a lost connection
MAX_TIMEOUTS = 3  # consecutive timeouts before the connection is dropped

_LAST_CONNECT: dict[str, float] = {}
_TELNET = re.compile(rb"\xff[\xfb-\xfe].|\xff[\xf0-\xfa]|\xff\xff", re.S)
_LINE = re.compile(r"^\W*([A-Za-z0-9_.]+)\s*(.*?)\s*$")


def parse_line(line: str) -> tuple[str, str] | None:
    """Split a reply line into (parameter, value)."""
    m = _LINE.match(line)
    if not m:
        return None
    return m.group(1).lower(), m.group(2)


class AsciiClient:
    """One persistent connection, commands are serialized and paced."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._lines: asyncio.Queue[str] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._last_send = 0.0
        self._next_connect = 0.0
        self._timeouts = 0

    # -- connection ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def _connect(self) -> None:
        if self.connected:
            return
        await self._disconnect()
        now = time.monotonic()
        if now < self._next_connect:
            raise ProcessorError(
                f"{self.host}: Verbindung verloren, neuer Versuch in "
                f"{self._next_connect - now:.0f} s"
            )
        # Never two connections to the same processor within a few seconds
        # (also across clients, e.g. config flow test followed by setup).
        wait = _LAST_CONNECT.get(self.host, 0.0) + CONNECT_SPACING - now
        if wait > 0:
            await asyncio.sleep(wait)
        _LAST_CONNECT[self.host] = time.monotonic()
        self._next_connect = time.monotonic() + RECONNECT_BACKOFF
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise ProcessorError(f"Keine Verbindung zu {self.host}:{self.port}: {err}") from err
        self._lines = asyncio.Queue()
        self._timeouts = 0
        self._reader_task = asyncio.get_running_loop().create_task(self._read_loop())
        _LOGGER.debug("Connected to %s:%s", self.host, self.port)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        buf = b""
        try:
            while True:
                chunk = await self._reader.read(4096)
                if not chunk:
                    break
                buf = _TELNET.sub(b"", buf + chunk)
                *lines, buf = re.split(rb"\r\n|\n|\r", buf)
                for raw in lines:
                    line = raw.decode("latin-1")
                    _LOGGER.debug("%s <- %r", self.host, line)
                    self._lines.put_nowait(line)
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            if self._writer is not None:
                self._writer.close()

    async def _disconnect(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._writer is not None:
            try:
                self._writer.close()
                await asyncio.wait_for(self._writer.wait_closed(), 2)
            except (OSError, asyncio.TimeoutError):
                pass
        self._reader = self._writer = None

    async def close(self) -> None:
        async with self._lock:
            if self.connected:
                try:
                    await self._send("exit")  # frees the session slot (max. 20)
                except ProcessorError:
                    pass
            await self._disconnect()

    # -- low level ----------------------------------------------------------

    async def _send(self, text: str) -> None:
        wait = self._last_send + COMMAND_SPACING - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        assert self._writer is not None
        _LOGGER.debug("%s -> %r", self.host, text)
        try:
            self._writer.write(text.encode("latin-1") + b"\r\n")
            await self._writer.drain()
        except OSError as err:
            await self._disconnect()
            raise ProcessorError(f"{self.host}: Senden fehlgeschlagen: {err}") from err
        self._last_send = time.monotonic()

    def _drain(self) -> None:
        while not self._lines.empty():
            self._lines.get_nowait()

    async def _next_line(self, deadline: float) -> str | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            return await asyncio.wait_for(self._lines.get(), remaining)
        except asyncio.TimeoutError:
            if not self.connected:
                raise ProcessorError(f"{self.host}: Verbindung getrennt") from None
            return None

    async def _wait_value(self, key: str, timeout: float) -> str:
        key = key.lower()
        deadline = time.monotonic() + timeout
        saw_blank = False
        while (line := await self._next_line(deadline)) is not None:
            if not line.strip():
                if not saw_blank:
                    saw_blank = True
                    deadline = min(deadline, time.monotonic() + BLANK_GRACE)
                continue
            parsed = parse_line(line)
            if parsed and parsed[0] == key:
                self._timeouts = 0
                return parsed[1]
        if saw_blank:
            raise ProcessorCommandError(f"{key}: vom Gerät nicht unterstützt")
        self._timeouts += 1
        if self._timeouts >= MAX_TIMEOUTS:
            await self._disconnect()
        raise ProcessorTimeout(f"{self.host}: keine Antwort auf {key}")

    # -- public -------------------------------------------------------------

    async def query(self, key: str) -> str:
        """Ask for a parameter, returns its value as text."""
        async with self._lock:
            await self._connect()
            self._drain()
            await self._send(f"{key} ?")
            return await self._wait_value(key, REPLY_TIMEOUT)

    async def set(self, key: str, value: str | int) -> str | None:
        """Set a parameter. Returns the echoed value, or None if not echoed."""
        async with self._lock:
            await self._connect()
            self._drain()
            await self._send(f"{key} {value}")
            try:
                return await self._wait_value(key, SET_TIMEOUT)
            except ProcessorTimeout:
                return None

    async def raw(self, text: str, wait: float = 2.0, idle: float = 0.5) -> list[str]:
        """Send any line and return everything received until it goes quiet."""
        async with self._lock:
            await self._connect()
            self._drain()
            await self._send(text.strip())
            lines: list[str] = []
            deadline = time.monotonic() + wait
            while True:
                end = min(deadline, time.monotonic() + idle) if lines else deadline
                line = await self._next_line(end)
                if line is None:
                    break
                lines.append(line)
            return lines
