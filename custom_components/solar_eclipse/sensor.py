from __future__ import annotations

import logging
import re
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from typing import Any, List, Optional, Tuple

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NASA_DECADE_URLS, ATTRIBUTION, SUPPORTED_REGIONS, JSEX_INDEX_URL, JSEX_REGION_LABELS, ECLIPSE_FALLBACK, DEFAULT_NUM_EVENTS, DEFAULT_UPDATE_HOUR, VERSION

# Optional Skyfield imports (declared in manifest requirements)
try:
    from skyfield.api import load, wgs84, Loader
    from math import acos, cos, sin
    SKYFIELD_AVAILABLE = True
except Exception:  # pragma: no cover - fallback when not installed
    SKYFIELD_AVAILABLE = False


# Simple i18n maps for attribute values (best-effort; keys are not localized by HA)
_REGION_I18N = {
    "en": {
        "Global": "Global",
        "Africa": "Africa",
        "Asia": "Asia",
        "Europe": "Europe",
        "North America": "North America",
        "South America": "South America",
        "Oceania": "Oceania",
        "Antarctica": "Antarctica",
    },
    "it": {
        "Global": "Globale",
        "Africa": "Africa",
        "Asia": "Asia",
        "Europe": "Europa",
        "North America": "Nord America",
        "South America": "Sud America",
        "Oceania": "Oceania",
        "Antarctica": "Antartide",
    },
    "de": {
        "Global": "Global",
        "Africa": "Afrika",
        "Asia": "Asien",
        "Europe": "Europa",
        "North America": "Nordamerika",
        "South America": "Südamerika",
        "Oceania": "Ozeanien",
        "Antarctica": "Antarktis",
    },
    "es": {
        "Global": "Global",
        "Africa": "África",
        "Asia": "Asia",
        "Europe": "Europa",
        "North America": "Norteamérica",
        "South America": "Sudamérica",
        "Oceania": "Oceanía",
        "Antarctica": "Antártida",
    },
    "fr": {
        "Global": "Global",
        "Africa": "Afrique",
        "Asia": "Asie",
        "Europe": "Europe",
        "North America": "Amérique du Nord",
        "South America": "Amérique du Sud",
        "Oceania": "Océanie",
        "Antarctica": "Antarctique",
    },
}

_TYPE_I18N = {
    "en": {"Total": "Total", "Annular": "Annular", "Partial": "Partial", "Hybrid": "Hybrid"},
    "it": {"Total": "Totale", "Annular": "Anulare", "Partial": "Parziale", "Hybrid": "Ibrida"},
    "de": {"Total": "Total", "Annular": "Ringförmig", "Partial": "Partiell", "Hybrid": "Hybrid"},
    "es": {"Total": "Total", "Annular": "Anular", "Partial": "Parcial", "Hybrid": "Híbrido"},
    "fr": {"Total": "Totale", "Annular": "Annulaire", "Partial": "Partielle", "Hybrid": "Hybride"},
}

def _attr_lang(hass: HomeAssistant) -> str:
    # HA server-side may not expose UI language; best effort using config or fallback to 'en'
    lang = getattr(hass.config, "language", None) or getattr(hass.config, "units", None)
    # If units object, ignore. Return 'en' as default
    if isinstance(lang, str) and len(lang) >= 2:
        return lang.split("-")[0].lower()
    return "en"

def _t_region(hass: HomeAssistant, region: Optional[str]) -> Optional[str]:
    if not region:
        return region
    lang = _attr_lang(hass)
    return _REGION_I18N.get(lang, _REGION_I18N["en"]).get(region, region)

def _t_type(hass: HomeAssistant, typ: Optional[str]) -> Optional[str]:
    if not typ:
        return typ
    lang = _attr_lang(hass)
    return _TYPE_I18N.get(lang, _TYPE_I18N["en"]).get(typ, typ)

@dataclass
class EclipseEvent:
    identifier: str
    date: datetime
    type: str
    start: Optional[datetime]
    end: Optional[datetime]
    region_text: Optional[str] = None


class EclipseCoordinator(DataUpdateCoordinator[List[EclipseEvent]]):
    def __init__(self, hass: HomeAssistant, install_skyfield: bool, latitude: float, longitude: float, region: str, num_events: int):
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
        self.num_events = max(1, min(10, int(num_events)))
        self._ephemeris = None
        # In-memory cache (24h TTL)
        self._cache_events: Optional[List[EclipseEvent]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = timedelta(hours=24)
        # Dedicated Skyfield data directory under HA storage
        self.skyfield_dir = hass.config.path(".storage/solar_eclipse_skyfield")
        # Limit concurrent Skyfield computations (CPU/RAM)
        self._sf_semaphore = asyncio.Semaphore(3)
        # Loaded UI translations for value localization
        self._translations: dict[str, str] = {}
        self._lang: Optional[str] = None

    async def async_load_translations(self, lang: str) -> None:
        try:
            self._translations = await async_get_translations(self.hass, lang, f"component.{DOMAIN}")
            self._lang = lang
        except Exception:
            self._translations = {}
            self._lang = None

    def translate_value(self, category: str, value: Optional[str]) -> Optional[str]:
        if not value:
            return value
        # Lookup key: component.<domain>.attr.<category>.<value>
        key = f"component.{DOMAIN}.attr.{category}.{value}"
        return self._translations.get(key, value)

    def _load_ephemeris_sync(self):
        # Runs in executor thread; uses dedicated directory
        loader = Loader(self.skyfield_dir)
        ts = loader.timescale()
        eph = loader("de421.bsp")
        return (ts, eph)

    async def _async_setup_skyfield(self) -> None:
        if not self.install_skyfield or not SKYFIELD_AVAILABLE or self._ephemeris is not None:
            return
        try:
            # Load ephemeris in background thread to avoid blocking
            self.logger.info("Loading Skyfield ephemeris (de421) in background...")
            self._ephemeris = await self.hass.async_add_executor_job(self._load_ephemeris_sync)
            self.logger.info("Skyfield ephemeris loaded (de421).")
        except Exception as err:
            self._ephemeris = None
            self.logger.warning("Skyfield ephemeris load failed: %s", err)

    def _cache_set(self, events: List[EclipseEvent]) -> None:
        self._cache_events = list(events)
        self._cache_timestamp = datetime.now(timezone.utc)

    def _cache_get(self) -> Optional[List[EclipseEvent]]:
        if self._cache_events and self._cache_timestamp:
            if datetime.now(timezone.utc) - self._cache_timestamp <= self._cache_ttl:
                return list(self._cache_events)
        return None

    async def _async_fetch_text(self, url: str) -> Optional[str]:
        session = async_get_clientsession(self.hass)
        headers = {
            "User-Agent": "HomeAssistant solar_eclipse integration (+https://github.com/matteoconti92/solar_eclipse)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.7",
        }
        delay = 1.0
        for attempt in range(3):
            try:
                async with session.get(url, timeout=10, headers=headers) as resp:
                    if resp.status != 200:
                        self.logger.debug("Fetch %s returned HTTP %s (attempt %s)", url, resp.status, attempt + 1)
                        raise RuntimeError(f"HTTP {resp.status}")
                    return await resp.text()
            except Exception as err:
                self.logger.debug("Fetch failed %s: %s (attempt %s)", url, err, attempt + 1)
                if attempt < 2:
                    await asyncio.sleep(delay)
                    delay *= 2
        return None

    async def _async_fetch_nasa(self) -> List[EclipseEvent]:
        events: List[EclipseEvent] = []
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
        pattern = re.compile(r"(20\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}).{0,120}?(Total|Annular|Partial|Hybrid|[TAPH]).{0,120}?(\d{2}:\d{2})", re.IGNORECASE | re.DOTALL)
        row_pattern = re.compile(r"<tr[\s\S]*?>[\s\S]*?<\/tr>", re.IGNORECASE)

        for url in NASA_DECADE_URLS:
            text = await self._async_fetch_text(url)
            if not text:
                continue
            rows = row_pattern.findall(text)
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
            # Also fallback to whole page scan for this decade
            if not events:
                for match in pattern.finditer(text):
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
                    events.append(EclipseEvent(identifier=identifier, date=dt, type=typ, start=None, end=None, region_text=None))
        # Dedup and sort
        if events:
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
            return True
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
        if nasa_events:
            self._cache_set(nasa_events)
            self.logger.info("Loaded %s events from SEfuture.", len(nasa_events))
        else:
            cached = self._cache_get()
            if cached:
                self.logger.info("Using cached SEfuture events (%s items).", len(cached))
                nasa_events = cached
            else:
                self.logger.warning("No SEfuture data and no cache; using minimal fallback list.")
                tmp: List[EclipseEvent] = []
                for item in ECLIPSE_FALLBACK:
                    y, m, d = [int(x) for x in item["identifier"].split("-")]
                    hh, mm = [int(x) for x in item["time_utc"].split(":")]
                    dt = datetime(y, m, d, hh, mm, tzinfo=timezone.utc)
                    tmp.append(EclipseEvent(identifier=item["identifier"], date=dt, type=item["type"], start=None, end=None, region_text=None))
                nasa_events = tmp

        now = datetime.now(timezone.utc)
        future = [e for e in nasa_events if e.date > now]
        future.sort(key=lambda e: e.date)

        if self.install_skyfield and SKYFIELD_AVAILABLE and self._ephemeris is not None:
            lat = float(self.latitude)
            lon = float(self.longitude)

            async def local_max_cov(evt: EclipseEvent) -> Optional[float]:
                async with self._sf_semaphore:
                    try:
                        result = await self.async_find_local_maximum(evt.date, lat, lon)
                        return result[1] if result else 0.0
                    except Exception as err:
                        self.logger.debug("Local max calc failed for %s: %s", evt.identifier, err)
                        return 0.0

            # Scan future events in batches until 3 visible are found
            visible: List[EclipseEvent] = []
            batch_size = 25
            max_scan = len(future)
            for start_idx in range(0, max_scan, batch_size):
                batch = future[start_idx:start_idx + batch_size]
                if not batch:
                    break
                coverages = await asyncio.gather(*(local_max_cov(e) for e in batch), return_exceptions=False)
                for e, c in zip(batch, coverages):
                    if c and c > 0.0:
                        visible.append(e)
                        if len(visible) >= self.num_events:
                            return visible[: self.num_events]
            return visible[: self.num_events]

        if future:
            try:
                vis_flags = await asyncio.gather(*(self._async_visible_in_region(e.identifier) for e in future))
                region_visible = [e for e, ok in zip(future, vis_flags) if ok]
                if region_visible:
                    return region_visible[: self.num_events]
                self.logger.info("No events matched region filter; falling back to first 3 future events.")
                return future[: self.num_events]
            except Exception as err:
                self.logger.debug("Region filtering failed: %s; falling back to first 3.", err)
                return future[: self.num_events]
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
        coverage = await self.async_calculate_coverage_percent(best_dt, lat, lon)
        if coverage is None:
            return None
        return best_dt, coverage

    async def async_find_contact_times(self, approx_when: datetime, lat: float, lon: float) -> Optional[Tuple[datetime, datetime]]:
        if not (self.install_skyfield and SKYFIELD_AVAILABLE and self._ephemeris is not None):
            return None
        # Find local max first
        local = await self.async_find_local_maximum(approx_when, lat, lon)
        if not local or local[1] <= 0.0:
            return None
        ts, eph = self._ephemeris
        earth = eph["earth"]
        sun = eph["sun"]
        moon = eph["moon"]
        observer = earth + wgs84.latlon(latitude_degrees=lat, longitude_degrees=lon)

        def coverage(dt: datetime) -> float:
            t = ts.from_datetime(dt)
            a_s = observer.at(t).observe(sun).apparent()
            a_m = observer.at(t).observe(moon).apparent()
            sep = a_s.separation_from(a_m).radians
            R = (16.0 / 60.0) * (3.1415926535 / 180.0)
            r = (15.5 / 60.0) * (3.1415926535 / 180.0)
            if sep >= R + r:
                return 0.0
            if sep <= abs(R - r):
                return 100.0 * (min(R, r) ** 2) / (R ** 2)
            from math import acos, sin
            def clamp(x: float) -> float:
                return max(-1.0, min(1.0, x))
            alpha = 2 * acos(clamp((sep * sep + R * R - r * r) / (2 * sep * R)))
            beta = 2 * acos(clamp((sep * sep + r * r - R * R) / (2 * sep * r)))
            area = 0.5 * (R * R * (alpha - sin(alpha)) + r * r * (beta - sin(beta)))
            return 100.0 * area / (3.1415926535 * (R ** 2))

        max_time = local[0]
        # Search backward until coverage ~ 0%
        start = max_time
        t = max_time
        step = timedelta(minutes=2)
        limit = max_time - timedelta(hours=4)
        while t > limit and coverage(t) > 0.1:
            start = t
            t -= step
        # Search forward until coverage ~ 0%
        end = max_time
        t = max_time
        limit = max_time + timedelta(hours=4)
        while t < limit and coverage(t) > 0.1:
            end = t
            t += step
        return (start, end)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    install_skyfield: bool = entry.options.get("install_skyfield", entry.data.get("install_skyfield", True))
    latitude: float = entry.options.get("latitude", entry.data.get("latitude", hass.config.latitude or 0.0))
    longitude: float = entry.options.get("longitude", entry.data.get("longitude", hass.config.longitude or 0.0))
    region: str = entry.options.get("region", entry.data.get("region", "Europe"))
    num_events: int = entry.options.get("num_events", entry.data.get("num_events", DEFAULT_NUM_EVENTS))
    update_hour: int = entry.options.get("update_hour", entry.data.get("update_hour", DEFAULT_UPDATE_HOUR))

    # Best-effort cleanup: remove stale Eclipse N sensors if num_events was reduced
    try:
        registry = er.async_get(hass)
        for ent in list(registry.entities.values()):
            if ent.config_entry_id != entry.entry_id:
                continue
            # Match sensors created with unique_id pattern: {entry_id}_eclipse{N}_date
            m = re.match(rf"{re.escape(entry.entry_id)}_eclipse(\d+)_date$", ent.unique_id)
            if not m:
                continue
            idx = int(m.group(1))
            if idx > int(num_events):
                registry.async_remove(ent.entity_id)
    except Exception:
        # Ignore cleanup errors; entities can also be removed manually
        pass

    coordinator = EclipseCoordinator(hass, install_skyfield, latitude, longitude, region, num_events)
    # Load translations for current UI language (best effort)
    ui_lang = getattr(hass.config, "language", None)
    if isinstance(ui_lang, str) and ui_lang:
        await coordinator.async_load_translations(ui_lang)
    entities: List[SensorEntity] = []
    for index in range(num_events):
        entities.append(EclipseAggregateSensor(coordinator, entry, index, update_hour))
    # Days until next eclipse
    entities.append(EclipseDaysUntilSensor(coordinator, entry))
    # Setup status tracking
    entities.append(EclipseSetupStatusSensor(coordinator, entry))

    # Register entities first so they exist
    async_add_entities(entities)

    # Perform complete setup: wait for initial data, ephemeris, and attribute computation
    coordinator.logger.info("Starting complete Solar Eclipse setup...")
    
    # Wait for coordinator to get initial data
    await coordinator.async_config_entry_first_refresh()
    coordinator.logger.info("Coordinator data loaded")
    
    # If Skyfield is enabled, ensure ephemeris is loaded and compute all attributes
    if coordinator.install_skyfield and SKYFIELD_AVAILABLE:
        coordinator.logger.info("Loading Skyfield ephemeris and computing attributes...")
        
        # Ensure ephemeris is loaded
        await coordinator._async_setup_skyfield()
        
        if coordinator._ephemeris is not None:
            coordinator.logger.info("Computing Skyfield attributes for all entities...")
            for entity in entities:
                if hasattr(entity, '_recompute'):
                    try:
                        await entity._recompute()
                    except Exception as err:
                        coordinator.logger.error("Skyfield recompute failed for %s: %s", entity.name, err)
            coordinator.logger.info("Solar Eclipse setup completed with Skyfield attributes")
        else:
            coordinator.logger.warning("Skyfield setup incomplete - ephemeris failed to load")
    else:
        coordinator.logger.info("Solar Eclipse setup completed (Skyfield disabled or unavailable)")
    
    # Final setup status update for all status sensors
    for entity in entities:
        if isinstance(entity, EclipseSetupStatusSensor):
            entity.async_write_ha_state()
    
    coordinator.logger.info("Solar Eclipse integration setup fully completed")


class EclipseBaseEntity(CoordinatorEntity[EclipseCoordinator], SensorEntity):
    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry, index: int) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.index = index
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Solar Eclipse",
            manufacturer="Eclipse predictions by NASA/GSFC",
            model="Solar Eclipse Advanced",
            sw_version=VERSION,
        )

    @property
    def _event(self) -> Optional[EclipseEvent]:
        data = self.coordinator.data or []
        if self.index < len(data):
            return data[self.index]
        return None


class EclipseAggregateSensor(EclipseBaseEntity):
    _attr_device_class = SensorDeviceClass.DATE
    _attr_should_poll = False

    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry, index: int, update_hour: int) -> None:
        super().__init__(coordinator, entry, index)
        self._attr_unique_id = f"{entry.entry_id}_eclipse{index+1}_date"
        self._attr_name = f"Eclipse {index+1} Date"
        self._attr_icon = "mdi:moon-waning-crescent"
        self._cached_coverage: Optional[float] = None
        self._cached_local_max_time: Optional[datetime] = None
        self._cached_local_max_coverage: Optional[float] = None
        self._cached_start_local: Optional[datetime] = None
        self._cached_end_local: Optional[datetime] = None
        self._cached_duration_minutes: Optional[float] = None
        self._unsub_midnight = None
        self._update_hour = int(update_hour)
        # Throttling state
        self._last_recompute_day: Optional[date] = None
        self._last_event_identifier: Optional[str] = None

    @property
    def native_value(self) -> Any:
        event = self._event
        # Date-only value
        return event.date.date() if event else None

    @property
    def extra_state_attributes(self):
        event = self._event
        # Build attributes in requested order: type, coverage, start_time, maximum_time, end_time, duration, region, source
        attrs = {}
        
        # 1. Type (if event present)
        if event:
            translated_type = self.coordinator.translate_value("type", event.type)
            attrs["type"] = _t_type(self.hass, translated_type)
        # 2. Coverage (Skyfield-derived)
        if self.coordinator.install_skyfield and SKYFIELD_AVAILABLE and self._cached_local_max_coverage is not None:
            attrs["coverage"] = f"{self._cached_local_max_coverage:.1f}%"
        
        # Debug logging
        self.coordinator.logger.debug("Checking Skyfield attributes: install=%s avail=%s ephemeris=%s", 
                                     self.coordinator.install_skyfield, SKYFIELD_AVAILABLE, self.coordinator._ephemeris is not None)
        self.coordinator.logger.debug("Cached values: coverage=%s duration=%s max_coverage=%s", 
                                     self._cached_coverage, self._cached_duration_minutes, self._cached_local_max_coverage)
        # 3-5. Time attributes (HH:MM in HA local timezone)
        if event:
            tz = dt_util.get_time_zone(self.hass.config.time_zone)
            def fmt_time(dt_val):
                if not dt_val:
                    return None
                try:
                    return dt_val.astimezone(tz).strftime("%H:%M")
                except Exception:
                    return None
            # Prefer Skyfield-derived local contacts; fallback to dataset
            start_dt = self._cached_start_local if (self.coordinator.install_skyfield and SKYFIELD_AVAILABLE) else event.start
            max_dt = self._cached_local_max_time if (self.coordinator.install_skyfield and SKYFIELD_AVAILABLE) else event.date
            end_dt = self._cached_end_local if (self.coordinator.install_skyfield and SKYFIELD_AVAILABLE) else event.end
            st = fmt_time(start_dt)
            mt = fmt_time(max_dt)
            et = fmt_time(end_dt)
            if st is not None:
                attrs["start_time"] = st
            if mt is not None:
                attrs["maximum_time"] = mt
            if et is not None:
                attrs["end_time"] = et
            self.coordinator.logger.debug("Time attributes: start=%s max=%s end=%s", st, mt, et)
        
        # 6. Duration (Skyfield-derived)
        if self.coordinator.install_skyfield and SKYFIELD_AVAILABLE and self._cached_duration_minutes is not None:
            attrs["duration"] = f"{self._cached_duration_minutes:.1f}"
        
        # 7. Region
        translated_region = self.coordinator.translate_value("region", self.coordinator.region)
        attrs["region"] = _t_region(self.hass, translated_region)
        
        # 8. Source
        attrs["source"] = NASA_DECADE_URLS[0]
        attrs["attribution"] = ATTRIBUTION
        # Optional NASA hints if available (appended after core fields)
        if event:
            if event.start is not None:
                attrs["start"] = event.start
            if event.end is not None:
                attrs["end"] = event.end
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Try an immediate compute if ephemeris already loaded, otherwise wait for coordinator update
        if self.coordinator.install_skyfield and SKYFIELD_AVAILABLE and self.coordinator._ephemeris is not None:
            await self._recompute()
        else:
            # Ensure state is available even before heavy data
            self._cached_coverage = None
            self._cached_local_max_time = None
            self._cached_local_max_coverage = None
            self._cached_start_local = None
            self._cached_end_local = None
            self._cached_duration_minutes = None
            self.async_write_ha_state()
        # Schedule daily recompute at configured hour
        self._unsub_midnight = async_track_time_change(
            self.hass, lambda now: self.hass.async_create_task(self._recompute()), hour=self._update_hour, minute=0, second=0
        )

    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        # When coordinator finishes first refresh and/or ephemeris loads, recompute attributes
        if self.coordinator.install_skyfield and SKYFIELD_AVAILABLE and self.coordinator._ephemeris is not None:
            self.hass.async_create_task(self._recompute())
        else:
            # Write base state so date updates propagate
            self.async_write_ha_state()
        
        # Also update status sensor
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None

    async def _recompute(self) -> None:
        event = self._event
        # Throttle: if same event and already recomputed today, skip heavy work
        today = datetime.now(timezone.utc).date()
        current_id = event.identifier if event else None
        if (
            self.coordinator.install_skyfield and SKYFIELD_AVAILABLE and self._last_recompute_day == today and self._last_event_identifier == current_id
        ):
            # Nothing changed; just write current state
            self.async_write_ha_state()
            return
        if not event:
            self._cached_coverage = None
            self._cached_local_max_time = None
            self._cached_local_max_coverage = None
            self._cached_start_local = None
            self._cached_end_local = None
            self._cached_duration_minutes = None
            self._last_recompute_day = today
            self._last_event_identifier = None
            self.async_write_ha_state()
            return
        if not (self.coordinator.install_skyfield and SKYFIELD_AVAILABLE and self.coordinator._ephemeris is not None):
            self._cached_coverage = None
            self._cached_local_max_time = None
            self._cached_local_max_coverage = None
            self._cached_start_local = None
            self._cached_end_local = None
            self._cached_duration_minutes = None
            self._last_recompute_day = today
            self._last_event_identifier = event.identifier
            self.async_write_ha_state()
            return
        lat = float(self.coordinator.latitude)
        lon = float(self.coordinator.longitude)
        self.coordinator.logger.debug("Computing Skyfield attributes for %s at %.4f,%.4f", event.identifier, lat, lon)
        cov_now = await self.coordinator.async_calculate_coverage_percent(event.date, lat, lon)
        local = await self.coordinator.async_find_local_maximum(event.date, lat, lon)
        contacts = await self.coordinator.async_find_contact_times(event.date, lat, lon)
        self.coordinator.logger.debug("Skyfield results: coverage=%.2f%%, local_max_coverage=%.2f%%, contacts=%s", 
                                    cov_now or 0, local[1] if local else 0, contacts[0] if contacts else None)
        self._cached_coverage = cov_now
        if local:
            self._cached_local_max_time = local[0]
            self._cached_local_max_coverage = local[1]
        else:
            self._cached_local_max_time = None
            self._cached_local_max_coverage = None
        if contacts:
            self._cached_start_local, self._cached_end_local = contacts
            # Calculate duration in minutes
            try:
                duration_delta = self._cached_end_local - self._cached_start_local
                self._cached_duration_minutes = duration_delta.total_seconds() / 60.0
            except Exception:
                self._cached_duration_minutes = None
        else:
            self._cached_start_local = None
            self._cached_end_local = None
            self._cached_duration_minutes = None
        self._last_recompute_day = today
        self._last_event_identifier = event.identifier
        self.async_write_ha_state()


class EclipseSetupStatusSensor(CoordinatorEntity[EclipseCoordinator], SensorEntity):
    _attr_name = "Solar Eclipse Setup Status"
    _attr_icon = "mdi:loading"
    _attr_should_poll = False

    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_setup_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Solar Eclipse",
            manufacturer="Eclipse predictions by NASA/GSFC",
            model="Solar Eclipse Advanced",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> str:
        """Return setup status."""
        if not self.coordinator.data:
            return "Loading data..."
        if self.coordinator.install_skyfield and SKYFIELD_AVAILABLE and self.coordinator._ephemeris is None:
            return "Loading Skyfield..."
        return "Ready"

    @property
    def extra_state_attributes(self):
        return {
            "data_loaded": bool(self.coordinator.data),
            "skyfield_installed": self.coordinator.install_skyfield and SKYFIELD_AVAILABLE,
            "ephemeris_loaded": self.coordinator._ephemeris is not None,
            "features_available": bool(self.coordinator.data) and (not self.coordinator.install_skyfield or self.coordinator._ephemeris is not None)
        }


class EclipseDaysUntilSensor(CoordinatorEntity[EclipseCoordinator], SensorEntity):
    _attr_name = "Days Until Next Eclipse"
    _attr_icon = "mdi:calendar-end"
    _attr_should_poll = False

    def __init__(self, coordinator: EclipseCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_days_until_next_eclipse"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Solar Eclipse",
            manufacturer="Eclipse predictions by NASA/GSFC",
            model="Solar Eclipse Advanced",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> Optional[int]:
        data = self.coordinator.data or []
        if not data:
            return None
        now = datetime.now(timezone.utc)
        future = [e for e in data if e.date >= now]
        if not future:
            return None
        next_event = min(future, key=lambda e: e.date)
        return max(0, (next_event.date.date() - now.date()).days)
