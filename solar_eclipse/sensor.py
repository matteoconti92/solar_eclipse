from __future__ import annotations

import logging
import re
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)
from homeassistant.helpers.event import async_track_utc_time_change

from .const import DOMAIN, NASA_URL, ATTRIBUTION, SUPPORTED_REGIONS, JSEX_INDEX_URL, JSEX_REGION_LABELS

# Optional Skyfield imports (declared in manifest requirements)
try:
    from skyfield.api import load, wgs84
    from math import acos, cos, sin
    SKYFIELD_AVAILABLE = True
except Exception:  # pragma: no cover - fallback when not installed
    SKYFIELD_AVAILABLE = False


@dataclass
class EclipseEvent:
    identifier: str
    date: datetime
    type: str
    start: Optional[datetime]
    end: Optional[datetime]
    region_text: Optional[str] = None


class EclipseCoordinator(DataUpdateCoordinator[List[EclipseEvent]]):
    def __init__(self, hass: HomeAssistant, install_skyfield: bool, latitude: float, longitude: float, region: str):
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name=f"{DOMAIN} coordinator",
            update_interval=timedelta(days=1),
        )
        self.install_skyfield = install_skyfield
        self.latitude = latitude
        self.longitude = longitude
        self.region = region if region in SUPPORTED_REGIONS else "Global"
        self._ephemeris = None

    async def _async_setup_skyfield(self) -> None:
        if not self.install_skyfield or not SKYFIELD_AVAILABLE or self._ephemeris is not None:
            return
        ts = load.timescale()
        eph = load("de421.bsp")
        self._ephemeris = (ts, eph)

    async def _async_fetch_text(self, url: str) -> Optional[str]:
        session = async_get_clientsession(self.hass)
        headers = {
            "User-Agent": "HomeAssistant solar_eclipse integration (+https://github.com/matteoconti92/solar_eclipse)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.7",
        }
        try:
            async with session.get(url, timeout=30, headers=headers) as resp:
                if resp.status != 200:
                    self.logger.debug("Fetch %s returned HTTP %s", url, resp.status)
                    return None
                return await resp.text()
        except Exception as err:
            self.logger.debug("Fetch failed %s: %s", url, err)
            return None

    async def _async_fetch_nasa(self) -> List[EclipseEvent]:
        text = await self._async_fetch_text(NASA_URL)
        events: List[EclipseEvent] = []
        if not text:
            self.logger.warning("NASA SEfuture page not available; no events loaded.")
            return events

        month_map = {
            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
            "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
        }
        type_map = {
            "T": "Total",
            "A": "Annular",
            "P": "Partial",
            "H": "Hybrid",
            "Total": "Total",
            "Annular": "Annular",
            "Partial": "Partial",
            "Hybrid": "Hybrid",
        }
        row_pattern = re.compile(r"<tr[\s\S]*?>[\s\S]*?<\/tr>", re.IGNORECASE)
        rows = row_pattern.findall(text)
        pattern = re.compile(r"(20\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}).{0,120}?(Total|Annular|Partial|Hybrid|[TAPH]).{0,120}?(\d{2}:\d{2})", re.IGNORECASE | re.DOTALL)
        for row in rows:
            match = pattern.search(row)
            if not match:
                continue
            year = int(match.group(1))
            mon_txt = match.group(2).title()
            day = int(match.group(3))
            typ_raw = match.group(4)
            time_txt = match.group(5)
            month = month_map.get(mon_txt)
            typ = type_map.get(typ_raw.title(), type_map.get(typ_raw.upper(), "Partial"))
            try:
                hour, minute = [int(x) for x in time_txt.split(":", 1)]
                dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            except Exception:
                dt = datetime(year, month, day, 0, 0, tzinfo=timezone.utc)
            identifier = f"{year:04d}-{month:02d}-{day:02d}"
            region_hint = self._extract_region_hint(row)
            events.append(EclipseEvent(identifier=identifier, date=dt, type=typ, start=None, end=None, region_text=region_hint))

        if not events:
            self.logger.warning("NASA SEfuture parsing returned 0 events; page structure may have changed.")
        else:
            uniq = {}
            for e in events:
                if e.identifier not in uniq or e.date < uniq[e.identifier].date:
                    uniq[e.identifier] = e
            events = list(uniq.values())
            events.sort(key=lambda e: e.date)
        return events

    def _extract_region_hint(self, row_text: str) -> Optional[str]:
        text = row_text.lower()
        region_keywords = {
            "Africa": ["africa"],
            "Asia": ["asia"],
            "Europe": ["europe"],
            "North America": ["north america", "usa", "united states", "canada", "mexico"],
            "South America": ["south america"],
            "Oceania": ["oceania", "australia", "new zealand"],
            "Antarctica": ["antarctica"],
        }
        for region, keys in region_keywords.items():
            for k in keys:
                if k in text:
                    return region
        return None

    async def _async_visible_in_region(self, identifier: str) -> bool:
        if self.region == "Global":
            return True
        index_text = await self._async_fetch_text(JSEX_INDEX_URL)
        if not index_text:
            return True  # fallback lenient
        label = JSEX_REGION_LABELS.get(self.region)
        if not label:
            return True
        m = re.search(rf"<a[^>]+href=\"([^\"]+)\"[^>]*>\s*{re.escape(label)}\s*<", index_text, re.IGNORECASE)
        if not m:
            return True
        href = m.group(1)
        if not href.startswith("http"):
            base = "https://eclipse.gsfc.nasa.gov/JSEX/"
            url = base + href.lstrip("./")
        else:
            url = href
        region_text = await self._async_fetch_text(url)
        if not region_text:
            return True
        y, mth, d = identifier.split("-")
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        try:
            mon_txt = month_names[int(mth) - 1]
        except Exception:
            mon_txt = mth
        p1 = re.compile(rf"{y}\s+{mon_txt}\s+{int(d)}", re.IGNORECASE)
        p2 = re.compile(rf"{int(d)}\s+{mon_txt}\s+{y}", re.IGNORECASE)
        return bool(p1.search(region_text) or p2.search(region_text))

    async def _async_update_data(self) -> List[EclipseEvent]:
        await self._async_setup_skyfield()
        nasa_events = await self._async_fetch_nasa()
        now = datetime.now(timezone.utc)
        future = [e for e in nasa_events if e.date > now]
        future.sort(key=lambda e: e.date)

        if self.install_skyfield and SKYFIELD_AVAILABLE and self._ephemeris is not None:
            lat = float(self.latitude)
            lon = float(self.longitude)
            async def cov(evt: EclipseEvent) -> Optional[float]:
                try:
                    return await self.async_calculate_coverage_percent(evt.date, lat, lon)
                except Exception as err:
                    self.logger.debug("Coverage calc failed for %s: %s", evt.identifier, err)
                    return None
            coverages = await asyncio.gather(*(cov(e) for e in future), return_exceptions=False)
            visible: List[EclipseEvent] = []
            for e, c in zip(future, coverages):
                if c is not None and c > 0.0:
                    visible.append(e)
            return visible[:3]

        if future:
            try:
                vis_flags = await asyncio.gather(*(self._async_visible_in_region(e.identifier) for e in future))
                region_visible = [e for e, ok in zip(future, vis_flags) if ok]
                if region_visible:
                    return region_visible[:3]
                self.logger.info("No events matched region filter; falling back to first 3 future events.")
                return future[:3]
            except Exception as err:
                self.logger.debug("Region filtering failed: %s; falling back to first 3.", err)
                return future[:3]
        return []

    async def async_calculate_coverage_percent(self, when: datetime, lat: float, lon: float) -> Optional[float]:
        if not (self.install_skyfield and SKYFIELD_AVAILABLE and self._ephemeris is not None):
            return None
        ts, eph = self._ephemeris
        t = ts.from_datetime(when)
        earth = eph["earth"]
        sun = eph["sun"]
        moon = eph["moon"]
        observer = earth + wgs84.latlon(latitude_degrees=lat, longitude_degrees=lon)
        astrometric_sun = observer.at(t).observe(sun).apparent()
        astrometric_moon = observer.at(t).observe(moon).apparent()
        sep_rad = astrometric_sun.separation_from(astrometric_moon).radians
        sun_radius_rad = (16.0 / 60.0) * (3.1415926535 / 180.0)
        moon_radius_rad = (15.5 / 60.0) * (3.1415926535 / 180.0)
        d = sep_rad
        R = sun_radius_rad
        r = moon_radius_rad
        if d >= R + r:
            return 0.0
        if d <= abs(R - r):
            covered = (3.1415926535 * (min(R, r) ** 2))
            return round(100.0 * covered / (3.1415926535 * (R ** 2)), 1)
        def clamp(x: float) -> float:
            return max(-1.0, min(1.0, x))
        alpha = 2 * acos(clamp((d * d + R * R - r * r) / (2 * d * R)))
        beta = 2 * acos(clamp((d * d + r * r - R * R) / (2 * d * r)))
        area = 0.5 * (R * R * (alpha - sin(alpha)) + r * r * (beta - sin(beta)))
        return round(100.0 * area / (3.1415926535 * (R ** 2)), 1)

    async def async_find_local_maximum(self, approx_when: datetime, lat: float, lon: float) -> Optional[Tuple[datetime, float]]:
        if not (self.install_skyfield and SKYFIELD_AVAILABLE and self._ephemeris is not None):
            return None
        ts, eph = self._ephemeris
        earth = eph["earth"]
        sun = eph["sun"]
        moon = eph["moon"]
        observer = earth + wgs84.latlon(latitude_degrees=lat, longitude_degrees=lon)

        def separation(dt: datetime) -> float:
            t = ts.from_datetime(dt)
            a_s = observer.at(t).observe(sun).apparent()
            a_m = observer.at(t).observe(moon).apparent()
            return a_s.separation_from(a_m).radians

        # Coarse scan ±3h every 5 minutes
        window = timedelta(hours=3)
        step = timedelta(minutes=5)
        start = approx_when - window
        end = approx_when + window
        num = int((end - start) / step) + 1
        best_dt = approx_when
        best_sep = separation(approx_when)
        for i in range(num):
            dt = start + i * step
            sep = separation(dt)
            if sep < best_sep:
                best_sep = sep
                best_dt = dt
        # Refine ±10 minutes every 1 minute
        refine_window = timedelta(minutes=10)
        refine_step = timedelta(minutes=1)
        start = best_dt - refine_window
        end = best_dt + refine_window
        num = int((end - start) / refine_step) + 1
        for i in range(num):
            dt = start + i * refine_step
            sep = separation(dt)
            if sep < best_sep:
                best_sep = sep
                best_dt = dt
        # Compute coverage at best_dt
        coverage = await self.async_calculate_coverage_percent(best_dt, lat, lon)
        if coverage is None:
            return None
        return best_dt, coverage


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    install_skyfield: bool = entry.data.get("install_skyfield", True)
    latitude: float = entry.data.get("latitude", hass.config.latitude or 0.0)
    longitude: float = entry.data.get("longitude", hass.config.longitude or 0.0)
    region: str = entry.data.get("region", "Europe")

    coordinator = EclipseCoordinator(hass, install_skyfield, latitude, longitude, region)
    await coordinator.async_config_entry_first_refresh()

    entities: List[SensorEntity] = []
    for index in range(3):
        entities.extend(
            [
                EclipseFieldSensor(coordinator, entry, index, field="date"),
                EclipseFieldSensor(coordinator, entry, index, field="start"),
                EclipseFieldSensor(coordinator, entry, index, field="end"),
                EclipseFieldSensor(coordinator, entry, index, field="type"),
                EclipseCoverageSensor(coordinator, entry, index),
                EclipseLocalMaxTimeSensor(coordinator, entry, index),
                EclipseLocalMaxCoverageSensor(coordinator, entry, index),
            ]
        )

    async_add_entities(entities)


class EclipseBaseEntity(CoordinatorEntity[EclipseCoordinator], SensorEntity):
    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry, index: int) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.index = index
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
    def _event(self) -> Optional[EclipseEvent]:
        data = self.coordinator.data or []
        if self.index < len(data):
            return data[self.index]
        return None


class EclipseFieldSensor(EclipseBaseEntity):
    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry, index: int, field: str) -> None:
        super().__init__(coordinator, entry, index)
        self.field = field
        self._attr_unique_id = f"{entry.entry_id}_eclipse{index+1}_{field}"
        nice_field = field.capitalize()
        self._attr_name = f"Eclipse {index+1} {nice_field}"
        if field in ("date", "start", "end"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> Any:
        event = self._event
        if not event:
            return None
        if self.field == "type":
            return event.type
        value = getattr(event, self.field)
        return value


class EclipseCoverageSensor(EclipseBaseEntity):
    _attr_native_unit_of_measurement = "%"
    _attr_should_poll = False

    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry, index: int) -> None:
        super().__init__(coordinator, entry, index)
        self._attr_unique_id = f"{entry.entry_id}_eclipse{index+1}_coverage"
        self._attr_name = f"Eclipse {index+1} Coverage"
        self._cached_value: Optional[float] = None
        self._unsub_midnight = None

    @property
    def native_value(self) -> Optional[float]:
        return self._cached_value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._recompute()
        self._unsub_midnight = async_track_utc_time_change(
            self.hass, lambda now: self.hass.async_create_task(self._recompute()), hour=1, minute=0
        )

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None

    async def _recompute(self) -> None:
        event = self._event
        if not event:
            self._cached_value = None
            self.async_write_ha_state()
            return
        lat = float(self.coordinator.latitude)
        lon = float(self.coordinator.longitude)
        if not (self.coordinator.install_skyfield and SKYFIELD_AVAILABLE and self.coordinator._ephemeris is not None):
            self._cached_value = None
            self.async_write_ha_state()
            return
        value = await self.coordinator.async_calculate_coverage_percent(event.date, lat, lon)
        self._cached_value = value
        self.async_write_ha_state()


class EclipseLocalMaxTimeSensor(EclipseBaseEntity):
    _attr_should_poll = False

    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry, index: int) -> None:
        super().__init__(coordinator, entry, index)
        self._attr_unique_id = f"{entry.entry_id}_eclipse{index+1}_local_max_time"
        self._attr_name = f"Eclipse {index+1} Local Maximum"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._cached_time: Optional[datetime] = None
        self._unsub_midnight = None

    @property
    def native_value(self) -> Optional[datetime]:
        return self._cached_time

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._recompute()
        self._unsub_midnight = async_track_utc_time_change(
            self.hass, lambda now: self.hass.async_create_task(self._recompute()), hour=1, minute=0
        )

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None

    async def _recompute(self) -> None:
        event = self._event
        if not event:
            self._cached_time = None
            self.async_write_ha_state()
            return
        if not (self.coordinator.install_skyfield and SKYFIELD_AVAILABLE and self.coordinator._ephemeris is not None):
            self._cached_time = None
            self.async_write_ha_state()
            return
        lat = float(self.coordinator.latitude)
        lon = float(self.coordinator.longitude)
        result = await self.coordinator.async_find_local_maximum(event.date, lat, lon)
        self._cached_time = result[0] if result else None
        self.async_write_ha_state()


class EclipseLocalMaxCoverageSensor(EclipseBaseEntity):
    _attr_native_unit_of_measurement = "%"
    _attr_should_poll = False

    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry, index: int) -> None:
        super().__init__(coordinator, entry, index)
        self._attr_unique_id = f"{entry.entry_id}_eclipse{index+1}_local_max_coverage"
        self._attr_name = f"Eclipse {index+1} Max Coverage"
        self._cached_value: Optional[float] = None
        self._unsub_midnight = None

    @property
    def native_value(self) -> Optional[float]:
        return self._cached_value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._recompute()
        self._unsub_midnight = async_track_utc_time_change(
            self.hass, lambda now: self.hass.async_create_task(self._recompute()), hour=1, minute=0
        )

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None

    async def _recompute(self) -> None:
        event = self._event
        if not event:
            self._cached_value = None
            self.async_write_ha_state()
            return
        if not (self.coordinator.install_skyfield and SKYFIELD_AVAILABLE and self.coordinator._ephemeris is not None):
            self._cached_value = None
            self.async_write_ha_state()
            return
        lat = float(self.coordinator.latitude)
        lon = float(self.coordinator.longitude)
        result = await self.coordinator.async_find_local_maximum(event.date, lat, lon)
        self._cached_value = result[1] if result else None
        self.async_write_ha_state()
