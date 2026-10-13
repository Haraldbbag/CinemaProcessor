"""Input (CP750) or macro (CP850/CP950) selection."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ProcessorConfigEntry, ProcessorCoordinator
from .entity import ProcessorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProcessorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ProcessorSource(entry.runtime_data)])


class ProcessorSource(ProcessorEntity, SelectEntity):
    def __init__(self, coordinator: ProcessorCoordinator) -> None:
        macros = coordinator.driver.uses_macros
        super().__init__(coordinator, "macro" if macros else "input")
        self._attr_translation_key = "macro" if macros else "input"

    @property
    def options(self) -> list[str]:
        options = list(self.coordinator.data.sources)
        current = self.current_option
        if current and current not in options:
            options.append(current)
        return options

    @property
    def current_option(self) -> str | None:
        return self.coordinator.data.source

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_select_source(option)
