"""Mute switch: on = muted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ProcessorConfigEntry, ProcessorCoordinator
from .entity import ProcessorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProcessorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ProcessorMute(entry.runtime_data)])


class ProcessorMute(ProcessorEntity, SwitchEntity):
    _attr_translation_key = "mute"

    def __init__(self, coordinator: ProcessorCoordinator) -> None:
        super().__init__(coordinator, "mute")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.driver.mute_supported

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.mute

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_mute(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_mute(False)
