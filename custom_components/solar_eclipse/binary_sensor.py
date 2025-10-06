from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, ATTRIBUTION, NASA_DECADE_URLS, DEFAULT_NUM_EVENTS, DEFAULT_UPDATE_HOUR, VERSION, DEFAULT_MIN_COVERAGE
from .sensor import EclipseCoordinator, EclipseEvent, SKYFIELD_AVAILABLE  # reuse coordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    install_skyfield: bool = entry.options.get("install_skyfield", entry.data.get("install_skyfield", True))
    latitude: float = entry.options.get("latitude", entry.data.get("latitude", hass.config.latitude or 0.0))
    longitude: float = entry.options.get("longitude", entry.data.get("longitude", hass.config.longitude or 0.0))
    region: str = entry.options.get("region", entry.data.get("region", "Europe"))
    num_events: int = entry.options.get("num_events", entry.data.get("num_events", DEFAULT_NUM_EVENTS))
    update_hour: int = entry.options.get("update_hour", entry.data.get("update_hour", DEFAULT_UPDATE_HOUR))
    min_coverage: int = entry.options.get("min_coverage", entry.data.get("min_coverage", DEFAULT_MIN_COVERAGE))

    coordinator = EclipseCoordinator(hass, install_skyfield, latitude, longitude, region, num_events, min_coverage)
    # Kick off first refresh in background to avoid blocking platform setup
    hass.async_create_task(coordinator.async_config_entry_first_refresh())

    entity = EclipseThisWeekBinarySensor(coordinator, entry, update_hour)
    async_add_entities([entity])


class EclipseThisWeekBinarySensor(BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry, update_hour: int) -> None:
        self.coordinator = coordinator
        self.entry = entry
        self._update_hour = int(update_hour)
        self._attr_unique_id = f"{entry.entry_id}_eclipse_this_week"
        self._attr_name = "Eclipse This Week"
        self._attr_is_on = False
        self._attr_icon = "mdi:telescope"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Solar Eclipse",
            manufacturer="Eclipse predictions by NASA/GSFC",
            model="Solar Eclipse Advanced",
            sw_version=VERSION,
        )

    @property
    def extra_state_attributes(self):
        return {"attribution": ATTRIBUTION, "source": NASA_DECADE_URLS[0]}

    @property
    def is_on(self) -> bool:
        return bool(self._attr_is_on)

    async def async_added_to_hass(self) -> None:
        await self._refresh()
        from homeassistant.helpers.event import async_track_time_change
        # Use local timezone hour
        self._unsub = async_track_time_change(
            self.hass, lambda now: self.hass.async_create_task(self._refresh()), hour=self._update_hour, minute=0, second=0
        )

    async def async_will_remove_from_hass(self) -> None:
        if hasattr(self, "_unsub") and self._unsub:
            self._unsub()
            self._unsub = None

    async def _refresh(self) -> None:
        data: List[EclipseEvent] = self.coordinator.data or []
        now = datetime.now(timezone.utc)
        within_week = now + timedelta(days=7)
        self._attr_is_on = any(e.date <= within_week for e in data if e.date >= now)
        self.async_write_ha_state()
