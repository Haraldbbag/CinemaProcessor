"""Config flow for cinema processors."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import create_driver
from .common import ProcessorAuthError, ProcessorError, ProcessorInfo
from .const import (
    ATMOS_MODELS,
    CONF_MACROS,
    CONF_MODEL,
    CONF_READ_ONLY,
    CONF_SCAN_INTERVAL,
    CONF_WEB_PASSWORD,
    CONF_WEB_USERNAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WEB_PASSWORD,
    DEFAULT_WEB_USERNAME,
    DOMAIN,
    MODEL_CP750,
    MODEL_CP850,
    MODEL_CP950,
    MODEL_NAMES,
)
from .coordinator import ProcessorConfigEntry

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _clean(model: str, user_input: dict[str, Any]) -> dict[str, Any]:
    data = {
        CONF_MODEL: model,
        CONF_HOST: user_input[CONF_HOST].strip(),
        CONF_PORT: user_input[CONF_PORT],
    }
    if model in ATMOS_MODELS and user_input.get(CONF_WEB_USERNAME):
        data[CONF_WEB_USERNAME] = user_input[CONF_WEB_USERNAME].strip()
        data[CONF_WEB_PASSWORD] = user_input.get(CONF_WEB_PASSWORD) or DEFAULT_WEB_PASSWORD
    return data


def _schema(model: str, with_name: bool) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
    }
    if with_name:
        fields[vol.Optional(CONF_NAME)] = str
    if model in ATMOS_MODELS:
        fields[vol.Optional(CONF_WEB_USERNAME, default=DEFAULT_WEB_USERNAME)] = str
        fields[vol.Optional(CONF_WEB_PASSWORD, default=DEFAULT_WEB_PASSWORD)] = _PASSWORD
    return vol.Schema(fields)


class ProcessorConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(entry: ProcessorConfigEntry) -> OptionsFlow:
        return ProcessorOptionsFlow()

    async def _try(self, data: dict[str, Any], errors: dict[str, str]) -> ProcessorInfo | None:
        driver = create_driver(self.hass, data)
        try:
            info = await driver.device_info()
        except ProcessorError as err:
            _LOGGER.debug("Cannot connect to %s: %s", data[CONF_HOST], err)
            errors["base"] = "cannot_connect"
            await driver.close()
            return None
        try:
            await driver.check_web()
        except ProcessorAuthError as err:
            _LOGGER.warning("Web login to %s failed: %s", data[CONF_HOST], err)
            errors["base"] = "invalid_auth"
            return None
        except ProcessorError as err:
            _LOGGER.warning("Web interface of %s: %s", data[CONF_HOST], err)
            errors["base"] = "web_unreachable"
            return None
        finally:
            await driver.close()
        return info

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="user", menu_options=[MODEL_CP750, MODEL_CP850, MODEL_CP950]
        )

    async def async_step_cp750(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_step_model(MODEL_CP750, user_input)

    async def async_step_cp850(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_step_model(MODEL_CP850, user_input)

    async def async_step_cp950(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_step_model(MODEL_CP950, user_input)

    async def _async_step_model(
        self, model: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(model, user_input)
            await self.async_set_unique_id(data[CONF_HOST])
            self._abort_if_unique_id_configured()
            if await self._try(data, errors) is not None:
                name = user_input.get(CONF_NAME) or f"Dolby {MODEL_NAMES[model]} {data[CONF_HOST]}"
                # Starts read-only: commands only after a test on the real processor.
                return self.async_create_entry(
                    title=name, data=data, options={CONF_READ_ONLY: True}
                )
        return self.async_show_form(
            step_id=model,
            data_schema=self.add_suggested_values_to_schema(_schema(model, True), user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        model = entry.data.get(CONF_MODEL, MODEL_CP850)
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(model, user_input)
            if CONF_WEB_USERNAME in data and not user_input.get(CONF_WEB_PASSWORD):
                # Empty password field = keep the stored one (or the factory default).
                data[CONF_WEB_PASSWORD] = entry.data.get(CONF_WEB_PASSWORD) or DEFAULT_WEB_PASSWORD
            if await self._try(data, errors) is not None:
                return self.async_update_reload_and_abort(
                    entry, unique_id=data[CONF_HOST], data=data
                )
        suggested = user_input or {
            k: v for k, v in entry.data.items() if k != CONF_WEB_PASSWORD
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(_schema(model, False), suggested),
            errors=errors,
        )


class ProcessorOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        fields: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=2, max=300)),
            vol.Required(CONF_READ_ONLY, default=opts.get(CONF_READ_ONLY, True)): bool,
        }
        if self.config_entry.data.get(CONF_MODEL) != MODEL_CP750:
            fields[
                vol.Optional(CONF_MACROS, description={"suggested_value": opts.get(CONF_MACROS, "")})
            ] = TextSelector(TextSelectorConfig(multiline=True))
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))
