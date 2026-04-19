"""Sensor platform for Xiaomi Ab Wheel."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AbWheelCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class AbWheelSensorDescription(SensorEntityDescription):
    field: str = ""
    enabled_default: bool = True
    entity_cat: EntityCategory | None = None


SENSOR_DESCRIPTIONS: tuple[AbWheelSensorDescription, ...] = (
    # ── Real-time workout ─────────────────────────────────────────────────
    AbWheelSensorDescription(
        key="train_state",
        field="train_state",
        name="Workout State",
        icon="mdi:dumbbell",
    ),
    AbWheelSensorDescription(
        key="reps",
        field="reps",
        name="Workout Reps",
        native_unit_of_measurement="reps",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:counter",
    ),
    AbWheelSensorDescription(
        key="calories",
        field="calories",
        name="Workout Calories",
        native_unit_of_measurement="cal",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fire",
    ),
    AbWheelSensorDescription(
        key="duration",
        field="duration",
        name="Workout Duration",
        native_unit_of_measurement="s",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer",
    ),
    AbWheelSensorDescription(
        key="frequency",
        field="frequency",
        name="Workout Frequency",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:metronome",
    ),
    AbWheelSensorDescription(
        key="breaks",
        field="breaks",
        name="Workout Breaks",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:pause-circle",
    ),
    # ── Today totals ──────────────────────────────────────────────────────
    AbWheelSensorDescription(
        key="today_reps",
        field="today_reps",
        name="Today Reps",
        native_unit_of_measurement="reps",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:counter",
    ),
    AbWheelSensorDescription(
        key="today_calories",
        field="today_calories",
        name="Today Calories",
        native_unit_of_measurement="cal",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:fire",
    ),
    AbWheelSensorDescription(
        key="today_duration",
        field="today_duration",
        name="Today Duration",
        native_unit_of_measurement="s",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:timer",
    ),
    AbWheelSensorDescription(
        key="today_workouts",
        field="today_workouts",
        name="Today Workouts",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:dumbbell",
    ),
    # ── Last workout ──────────────────────────────────────────────────────
    AbWheelSensorDescription(
        key="last_reps",
        field="last_reps",
        name="Last Reps",
        native_unit_of_measurement="reps",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:counter",
    ),
    AbWheelSensorDescription(
        key="last_calories",
        field="last_calories",
        name="Last Calories",
        native_unit_of_measurement="cal",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fire",
    ),
    AbWheelSensorDescription(
        key="last_duration",
        field="last_duration",
        name="Last Duration",
        native_unit_of_measurement="s",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer",
    ),
    AbWheelSensorDescription(
        key="last_avg_freq",
        field="last_avg_freq",
        name="Last Avg Freq",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:metronome",
    ),
    AbWheelSensorDescription(
        key="last_max_freq",
        field="last_max_freq",
        name="Last Max Freq",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:metronome-tick",
    ),
    AbWheelSensorDescription(
        key="last_start_time",
        field="last_start_time",
        name="Last Time",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock",
    ),
    # ── Device / diagnostic ───────────────────────────────────────────────
    AbWheelSensorDescription(
        key="battery",
        field="battery",
        name="Battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
        entity_cat=EntityCategory.DIAGNOSTIC,
    ),
    AbWheelSensorDescription(
        key="offline_count",
        field="offline_count",
        name="Offline Records",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:database",
        entity_cat=EntityCategory.DIAGNOSTIC,
    ),
    AbWheelSensorDescription(
        key="journal_entries",
        field="journal_entries",
        name="Journal Entries",
        native_unit_of_measurement="entries",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:book-clock",
        entity_cat=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AbWheelCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AbWheelSensor(coordinator, entry, desc) for desc in SENSOR_DESCRIPTIONS
    )


class AbWheelSensor(CoordinatorEntity[AbWheelCoordinator], SensorEntity):
    """A single Ab Wheel sensor entity."""

    entity_description: AbWheelSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AbWheelCoordinator,
        entry: ConfigEntry,
        description: AbWheelSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator._mac}-{description.key}"
        self._attr_entity_registry_enabled_default = description.enabled_default
        if description.entity_cat is not None:
            self._attr_entity_category = description.entity_cat

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
    def native_value(self) -> Any:
        val = self.coordinator.data.get(self.entity_description.field)
        if self.entity_description.key == "last_start_time":
            if not val:
                return None
            try:
                return datetime.fromtimestamp(int(val), tz=timezone.utc)
            except (ValueError, OSError, TypeError):
                return None
        return val

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.key == "offline_count":
            records = self.coordinator.data.get("offline_records", [])
            if records:
                return {"records": records}
        return None
