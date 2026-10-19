"""Dolby CP750 / CP850 / CP950(A) driver (no Home Assistant imports)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from .ascii_client import AsciiClient
from .common import (
    ProcessorAuthError,
    ProcessorCommandError,
    ProcessorData,
    ProcessorError,
    ProcessorInfo,
)
from .const import ATMOS_MODELS, MODEL_CP750, MODEL_NAMES
from .web_client import AtmosTracker, WebClient

if TYPE_CHECKING:
    import aiohttp

_LOGGER = logging.getLogger(__name__)

# CP750 inputs: value of cp750.sys.input_mode -> name shown in Home Assistant
CP750_INPUTS = {
    "dig_1": "Digital 1",
    "dig_2": "Digital 2",
    "dig_3": "Digital 3",
    "dig_4": "Digital 4",
    "analog": "Multi-Ch Analog",
    "non_sync": "NonSync",
    "mic": "Mic",
}


def parse_fader(value: str) -> float:
    """'70' -> 7.0 (fader x 10, CP750 manual); '7.0' -> 7.0."""
    text = value.strip().strip('"')
    if "." in text:
        return round(float(text), 1)
    return round(int(text) / 10, 1)


def parse_bool(value: str) -> bool:
    text = value.strip().lower()
    if text in ("1", "on", "true", "yes"):
        return True
    if text in ("0", "off", "false", "no"):
        return False
    raise ValueError(value)


def parse_macro_list(text: str) -> dict[int, str]:
    """Option text -> {macro number: name}. 'N=Name' lines, or plain names numbered 1, 2, ..."""
    macros: dict[int, str] = {}
    number = 0
    for line in (text or "").replace(";", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\s*[=:]\s*(.+)$", line)
        if m:
            number = int(m.group(1))
            macros[number] = m.group(2).strip()
        else:
            number += 1
            macros[number] = line
    return macros


class DolbyDriver:
    manufacturer = "Dolby"

    def __init__(
        self,
        host: str,
        port: int,
        model: str,
        web_username: str | None = None,
        web_password: str | None = None,
        macros_text: str = "",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.model = model
        self.prefix = "cp750." if model == MODEL_CP750 else ""
        self.ascii = AsciiClient(host, port)
        self.web: WebClient | None = None
        if model in ATMOS_MODELS and web_username:
            self.web = WebClient(host, web_username, web_password or "", session)
        self.atmos = AtmosTracker()
        self.configured_macros = parse_macro_list(macros_text)
        self.learned_macros: dict[int, str] = {}
        self.learned_changed = False
        self.mute_supported = True
        self._last_macro: int | None = None

    # -- capabilities -------------------------------------------------------

    @property
    def uses_macros(self) -> bool:
        return self.model != MODEL_CP750

    @property
    def has_atmos(self) -> bool:
        return self.web is not None

    def _p(self, key: str) -> str:
        return self.prefix + key

    def macros(self) -> dict[int, str]:
        merged = dict(self.learned_macros)
        merged.update(self.configured_macros)
        return dict(sorted(merged.items()))

    # -- setup --------------------------------------------------------------

    async def device_info(self) -> ProcessorInfo:
        """Connects and checks that the processor answers (raises otherwise)."""
        await self.ascii.query(self._p("sys.fader"))
        version = None
        try:
            version = (await self.ascii.query(self._p("sysinfo.version"))).strip('"') or None
        except ProcessorError as err:
            _LOGGER.debug("%s: no version: %s", self.ascii.host, err)
        return ProcessorInfo(self.manufacturer, MODEL_NAMES[self.model], version)

    async def check_web(self) -> None:
        """Raises ProcessorAuthError / ProcessorError if the web login does not work."""
        if self.web is not None:
            await self.web.poll()

    async def close(self) -> None:
        await self.ascii.close()
        if self.web is not None:
            await self.web.close()

    # -- reading ------------------------------------------------------------

    async def update(self, data: ProcessorData) -> None:
        q = self.ascii.query
        data.fader = parse_fader(await q(self._p("sys.fader")))
        if self.mute_supported:
            try:
                data.mute = parse_bool(await q(self._p("sys.mute")))
            except ProcessorCommandError:
                _LOGGER.warning("%s: sys.mute wird nicht unterstützt", self.ascii.host)
                self.mute_supported = False
                data.mute = None
        if self.uses_macros:
            await self._update_macro(data)
        else:
            mode = (await q(self._p("sys.input_mode"))).lower()
            data.source = CP750_INPUTS.get(mode, mode)
            data.sources = list(CP750_INPUTS.values())
        if self.web is not None:
            await self._update_atmos(data)
        else:
            data.force_pcm = None

    async def _update_macro(self, data: ProcessorData) -> None:
        number_text = await self.ascii.query("sys.macro_preset")
        name: str | None = None
        try:
            name = (await self.ascii.query("sys.macro_name")).strip().strip('"') or None
        except ProcessorCommandError:
            pass
        try:
            number = int(number_text.strip().strip('"'))
        except ValueError:
            number = None
        if number is not None and number != self._last_macro:
            if self._last_macro is not None:
                # The processor reverts "ignore Dolby Atmos" on every macro change.
                self.atmos.force_pcm = False
            self._last_macro = number
        if number is not None and name and self.learned_macros.get(number) != name:
            self.learned_macros[number] = name
            self.learned_changed = True
        macros = self.macros()
        if number is not None and number in macros:
            data.source = macros[number]
        else:
            data.source = name or (f"Makro {number}" if number is not None else None)
        data.sources = list(macros.values())

    async def _update_atmos(self, data: ProcessorData) -> None:
        assert self.web is not None
        try:
            self.atmos.feed(await self.web.poll())
        except ProcessorAuthError as err:
            data.web_error = str(err)
            data.atmos = None
            return
        except ProcessorError as err:
            data.web_error = str(err)
            data.atmos = None
            return
        data.web_error = None
        data.atmos = self.atmos.active()
        data.atmos_attrs = self.atmos.attributes()
        data.force_pcm = self.atmos.force_pcm

    # -- commands -----------------------------------------------------------

    async def set_fader(self, value: float) -> None:
        value = min(10.0, max(0.0, value))
        await self.ascii.set(self._p("sys.fader"), int(round(value * 10)))

    async def step_fader(self, tenths: int) -> None:
        await self.ascii.set(self._p("ctrl.fader_delta"), tenths)

    async def set_mute(self, mute: bool) -> None:
        await self.ascii.set(self._p("sys.mute"), 1 if mute else 0)

    async def set_force_pcm(self, force: bool) -> None:
        """'ignore Dolby Atmos' on the web UI: True = play the PCM fallback."""
        if self.web is None:
            raise ProcessorCommandError("Nur mit Zugangsdaten der Weboberfläche möglich")
        await self.web.set_force_pcm(force)

    async def select_source(self, name: str) -> None:
        if self.uses_macros:
            for number, macro in self.macros().items():
                if macro == name:
                    await self.ascii.set("sys.macro_preset", number)
                    return
            m = re.match(r"^Makro (\d+)$", name)
            if m:
                await self.ascii.set("sys.macro_preset", int(m.group(1)))
                return
            raise ProcessorCommandError(f"Unbekanntes Makro: {name}")
        for mode, label in CP750_INPUTS.items():
            if label == name:
                await self.ascii.set(self._p("sys.input_mode"), mode)
                return
        raise ProcessorCommandError(f"Unbekannter Eingang: {name}")

    @staticmethod
    def is_read_command(command: str) -> bool:
        text = command.strip().lower()
        return text.endswith("?") or text in ("help", "status")

    async def send_raw(self, command: str) -> list[str]:
        return [line for line in await self.ascii.raw(command) if line.strip()]
