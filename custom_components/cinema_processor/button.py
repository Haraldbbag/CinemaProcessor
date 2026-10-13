"""Fader +0.1 / -0.1 (relative, so the front-panel knob is never overwritten)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ProcessorConfigEntry, ProcessorCoordinator
from .entity import ProcessorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProcessorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data
    async_add_entities([ProcessorFaderStep(c, 1), ProcessorFaderStep(c, -1)])


class ProcessorFaderStep(ProcessorEntity, ButtonEntity):
    def __init__(self, coordinator: ProcessorCoordinator, tenths: int) -> None:
        key = "fader_up" if tenths > 0 else "fader_down"
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._tenths = tenths

    async def async_press(self) -> None:
        await self.coordinator.async_step_fader(self._tenths)
