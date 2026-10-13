"""Main fader (volume) 0.0 .. 10.0, 7.0 = reference level."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ProcessorConfigEntry, ProcessorCoordinator
from .entity import ProcessorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProcessorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ProcessorFader(entry.runtime_data)])


class ProcessorFader(ProcessorEntity, NumberEntity):
    _attr_translation_key = "fader"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 10.0
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: ProcessorCoordinator) -> None:
        super().__init__(coordinator, "fader")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.fader

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_fader(value)
