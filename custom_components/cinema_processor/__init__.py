"""Cinema sound processors (Dolby CP750, CP850, CP950/CP950A)."""

from __future__ import annotations

from typing import Any, Mapping

import voluptuous as vol

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .common import ProcessorError
from .const import (
    CONF_INPUTS,
    CONF_MACROS,
    CONF_MODEL,
    CONF_WEB_PASSWORD,
    CONF_WEB_USERNAME,
    DEFAULT_PORT,
    DOMAIN,
    MODEL_CP850,
)
from .coordinator import ProcessorConfigEntry, ProcessorCoordinator
from .dolby import DolbyDriver

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SEND_COMMAND = "send_command"
SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("command"): cv.string,
    }
)


def create_driver(
    hass: HomeAssistant | None,
    data: Mapping[str, Any],
    options: Mapping[str, Any] | None = None,
) -> DolbyDriver:
    """Driver for a config entry's data (also used by the config flow)."""
    return DolbyDriver(
        data[CONF_HOST],
        data.get(CONF_PORT, DEFAULT_PORT),
        data.get(CONF_MODEL, MODEL_CP850),
        data.get(CONF_WEB_USERNAME),
        data.get(CONF_WEB_PASSWORD),
        (options or {}).get(CONF_MACROS, ""),
        (options or {}).get(CONF_INPUTS, ""),
        async_get_clientsession(hass) if hass is not None else None,
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def send_command(call: ServiceCall) -> ServiceResponse:
        device = dr.async_get(hass).async_get(call.data["device_id"])
        coordinator: ProcessorCoordinator | None = None
        if device is not None:
            for entry_id in device.config_entries:
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
                    coordinator = entry.runtime_data
                    break
        if coordinator is None:
            raise ServiceValidationError("Kein eingerichteter Prozessor für dieses Gerät.")
        replies = await coordinator.async_send_raw(call.data["command"])
        return {"replies": replies}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        send_command,
        schema=SEND_COMMAND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ProcessorConfigEntry) -> bool:
    driver = create_driver(hass, entry.data, entry.options)
    try:
        info = await driver.device_info()
    except ProcessorError as err:
        await driver.close()
        raise ConfigEntryNotReady(f"{entry.data[CONF_HOST]} nicht erreichbar: {err}") from err

    coordinator = ProcessorCoordinator(hass, entry, driver, info)
    await coordinator.async_load_macros()
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await driver.close()
        raise
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ProcessorConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ProcessorConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
