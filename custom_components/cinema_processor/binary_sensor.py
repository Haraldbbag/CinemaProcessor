"""Dolby Atmos playing (CP850/CP950A) and connection state."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
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
    entities: list[BinarySensorEntity] = [ProcessorConnected(c)]
    if c.driver.has_atmos:
        entities.append(ProcessorAtmos(c))
    async_add_entities(entities)


class ProcessorAtmos(ProcessorEntity, BinarySensorEntity):
    """On while Dolby Atmos is actually rendered (not just an Atmos macro selected)."""

    _attr_translation_key = "atmos"

    def __init__(self, coordinator: ProcessorCoordinator) -> None:
        super().__init__(coordinator, "atmos")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.atmos is not None

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.atmos

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.data.atmos_attrs


class ProcessorConnected(ProcessorEntity, BinarySensorEntity):
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ProcessorCoordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success
