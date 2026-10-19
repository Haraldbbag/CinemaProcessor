"""Mute switch (on = muted) and "ignore Dolby Atmos" (on = PCM fallback forced)."""

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
    c = entry.runtime_data
    entities: list[SwitchEntity] = [ProcessorMute(c)]
    if c.driver.has_atmos:
        entities.append(ProcessorForcePcm(c))
    async_add_entities(entities)


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


class ProcessorForcePcm(ProcessorEntity, SwitchEntity):
    """"ignore Dolby Atmos": on = the processor plays the PCM fallback.

    Reverts to Atmos by itself on macro change or reboot of the processor.
    """

    _attr_translation_key = "force_pcm"

    def __init__(self, coordinator: ProcessorCoordinator) -> None:
        super().__init__(coordinator, "force_pcm")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.web_error is None

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.force_pcm

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_force_pcm(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_force_pcm(False)
