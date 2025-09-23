from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, ATTRIBUTION, NASA_URL
from .sensor import EclipseCoordinator, EclipseEvent, SKYFIELD_AVAILABLE  # reuse coordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    install_skyfield: bool = entry.data.get("install_skyfield", True)
    latitude: float = entry.data.get("latitude", hass.config.latitude or 0.0)
    longitude: float = entry.data.get("longitude", hass.config.longitude or 0.0)
    region: str = entry.data.get("region", "Europe")

    coordinator = EclipseCoordinator(hass, install_skyfield, latitude, longitude, region)
    await coordinator.async_config_entry_first_refresh()

    entity = EclipseThisWeekBinarySensor(coordinator, entry)
    async_add_entities([entity])


class EclipseThisWeekBinarySensor(BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_eclipse_this_week"
        self._attr_name = "Eclipse This Week"
        self._attr_is_on = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Solar Eclipse",
            manufacturer="NASA/GSFC + Skyfield",
            model="Catalog + Local coverage",
        )

    @property
    def extra_state_attributes(self):
        return {"attribution": ATTRIBUTION, "source": NASA_URL}

    @property
    def is_on(self) -> bool:
        return bool(self._attr_is_on)

    async def async_added_to_hass(self) -> None:
        await self._refresh()
        # refresh daily at 01:00 UTC like coverage sensors
        from homeassistant.helpers.event import async_track_utc_time_change
        self._unsub = async_track_utc_time_change(
            self.hass, lambda now: self.hass.async_create_task(self._refresh()), hour=1, minute=0
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
