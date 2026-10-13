"""Polling coordinator."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Awaitable, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .common import ProcessorData, ProcessorError, ProcessorInfo
from .const import (
    CONF_READ_ONLY,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    REFRESH_DELAY,
)
from .dolby import DolbyDriver

_LOGGER = logging.getLogger(__name__)

type ProcessorConfigEntry = ConfigEntry[ProcessorCoordinator]


class ProcessorCoordinator(DataUpdateCoordinator[ProcessorData]):
    config_entry: ProcessorConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ProcessorConfigEntry,
        driver: DolbyDriver,
        info: ProcessorInfo,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.driver = driver
        self.info = info
        self.read_only: bool = entry.options.get(CONF_READ_ONLY, True)
        self._data = ProcessorData()
        self._store: Store[dict[str, str]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.macros")
        self._refresh_task: asyncio.Task | None = None
        self._web_error_logged: str | None = None

    async def async_load_macros(self) -> None:
        stored = await self._store.async_load() or {}
        self.driver.learned_macros = {int(k): v for k, v in stored.items() if k.isdigit()}

    # -- polling ------------------------------------------------------------

    async def _async_update_data(self) -> ProcessorData:
        d = self._data
        try:
            await self.driver.update(d)
        except ProcessorError as err:
            raise UpdateFailed(str(err)) from err
        if self.driver.learned_changed:
            self.driver.learned_changed = False
            learned = {str(k): v for k, v in self.driver.learned_macros.items()}
            self._store.async_delay_save(lambda: learned, 5)
        if d.web_error != self._web_error_logged:
            if d.web_error:
                _LOGGER.warning("%s: Atmos-Status nicht lesbar: %s", self.config_entry.title, d.web_error)
            self._web_error_logged = d.web_error
        return d

    # -- commands -----------------------------------------------------------

    def _check_writable(self) -> None:
        if self.read_only:
            raise HomeAssistantError(
                f"{self.config_entry.title} ist auf „Nur lesen“ gestellt (Optionen der Integration)."
            )

    async def _do(self, action: Callable[[], Awaitable[object]]) -> None:
        self._check_writable()
        try:
            await action()
        except ProcessorError as err:
            raise HomeAssistantError(f"{self.config_entry.title}: {err}") from err
        self._schedule_refresh()

    async def async_set_fader(self, value: float) -> None:
        await self._do(lambda: self.driver.set_fader(value))

    async def async_step_fader(self, tenths: int) -> None:
        await self._do(lambda: self.driver.step_fader(tenths))

    async def async_set_mute(self, mute: bool) -> None:
        await self._do(lambda: self.driver.set_mute(mute))

    async def async_select_source(self, name: str) -> None:
        await self._do(lambda: self.driver.select_source(name))

    async def async_send_raw(self, command: str) -> list[str]:
        if not self.driver.is_read_command(command):
            self._check_writable()
        try:
            replies = await self.driver.send_raw(command)
        except ProcessorError as err:
            raise HomeAssistantError(f"{self.config_entry.title}: {err}") from err
        self._schedule_refresh()
        return replies

    def _schedule_refresh(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = self.config_entry.async_create_background_task(
            self.hass, self._delayed_refresh(), f"{DOMAIN}_refresh"
        )

    async def _delayed_refresh(self) -> None:
        await asyncio.sleep(REFRESH_DELAY)
        await self.async_refresh()

    async def async_shutdown(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
        await super().async_shutdown()
        await self.driver.close()
