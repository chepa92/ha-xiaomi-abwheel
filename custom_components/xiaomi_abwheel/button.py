"""Button platform for Xiaomi Ab Wheel – manual BLE connect & clear journal."""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AbWheelCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AbWheelCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        AbWheelConnectButton(coordinator, entry),
        AbWheelClearJournalButton(coordinator, entry),
    ])


class _AbWheelButtonBase(ButtonEntity):
    """Base class for Ab Wheel buttons with shared device_info."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AbWheelCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator._mac)},
            name="Xiaomi Ab Wheel",
            manufacturer="Xiaomi",
            model="jfl001",
        )


class AbWheelConnectButton(_AbWheelButtonBase):
    """Button to trigger a manual BLE connection attempt."""

    _attr_icon = "mdi:bluetooth-connect"
    _attr_name = "Connect"

    def __init__(self, coordinator: AbWheelCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator._mac}-connect"

    async def async_press(self) -> None:
        _LOGGER.info("Manual connect button pressed")
        await self._coordinator.async_manual_connect()


class AbWheelClearJournalButton(_AbWheelButtonBase):
    """Button to clear the workout journal file."""

    _attr_icon = "mdi:delete-clock"
    _attr_name = "Clear Journal"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AbWheelCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator._mac}-clear_journal"

    async def async_press(self) -> None:
        _LOGGER.info("Clear journal button pressed")
        await self._coordinator.async_clear_journal()
