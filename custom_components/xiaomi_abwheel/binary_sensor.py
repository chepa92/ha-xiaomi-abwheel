"""Binary sensor platform for Xiaomi Ab Wheel – BLE connection status."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AbWheelCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AbWheelCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AbWheelConnectedSensor(coordinator, entry)])


class AbWheelConnectedSensor(CoordinatorEntity[AbWheelCoordinator], BinarySensorEntity):
    """Binary sensor showing BLE connection status."""

    _attr_has_entity_name = True
    _attr_name = "Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator: AbWheelCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator._mac}-connected"

    @property
    def device_info(self) -> DeviceInfo:
        data = self.coordinator.data or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator._mac)},
            name="Xiaomi Ab Wheel",
            manufacturer="Xiaomi",
            model="jfl001",
            sw_version=data.get("firmware", ""),
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.get("connected", False)

    @property
    def available(self) -> bool:
        return True
