import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN, SUPPORTED_REGIONS, DEFAULT_NUM_EVENTS, DEFAULT_UPDATE_HOUR, DEFAULT_MIN_COVERAGE

class SolarEclipseConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Solar Eclipse integration."""

    VERSION = 2

    def _format_time(self, hour: int) -> str:
        hour = max(0, min(23, int(hour)))
        return f"{hour:02d}:00"

    def _parse_hour(self, value) -> int:
        try:
            if isinstance(value, str) and ":" in value:
                parts = value.split(":")
                return max(0, min(23, int(parts[0])))
            return max(0, min(23, int(value)))
        except Exception:
            return DEFAULT_UPDATE_HOUR

    async def async_step_user(self, user_input=None):
        # Enforce single instance
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            num_events = int(user_input.get("num_events", DEFAULT_NUM_EVENTS))
            update_hour = self._parse_hour(user_input.get("update_hour", DEFAULT_UPDATE_HOUR))
            min_coverage = int(user_input.get("min_coverage", DEFAULT_MIN_COVERAGE))
            min_coverage = max(0, min(100, min_coverage))
            num_events = max(1, min(10, num_events))
            install_skyfield = bool(user_input.get("install_skyfield", True))
            self._data = {
                "install_skyfield": install_skyfield,
                "num_events": num_events,
                "update_hour": update_hour,
                "min_coverage": min_coverage,
            }
            if install_skyfield:
                return await self.async_step_coords()
            return await self.async_step_region()

        schema = vol.Schema({
            vol.Required("install_skyfield", default=True): bool,
            vol.Required("num_events", default=DEFAULT_NUM_EVENTS): int,
            vol.Required("update_hour", default=self._format_time(DEFAULT_UPDATE_HOUR)): str,
            vol.Required("min_coverage", default=DEFAULT_MIN_COVERAGE): int,
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_coords(self, user_input=None):
        if user_input is not None:
            data = {**getattr(self, "_data", {}), **user_input}
            return self.async_create_entry(title="Solar Eclipse", data=data)

        lat = self.hass.config.latitude or 0.0
        lon = self.hass.config.longitude or 0.0
        schema = vol.Schema({
            vol.Required("latitude", default=lat): float,
            vol.Required("longitude", default=lon): float,
        })
        return self.async_show_form(step_id="coords", data_schema=schema)

    async def async_step_region(self, user_input=None):
        if user_input is not None:
            data = {**getattr(self, "_data", {}), **user_input}
            return self.async_create_entry(title="Solar Eclipse", data=data)

        schema = vol.Schema({
            vol.Required("region", default="Europe"): vol.In(SUPPORTED_REGIONS),
        })
        return self.async_show_form(step_id="region", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SolarEclipseOptionsFlow(config_entry)


class SolarEclipseOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry
        self._opts = dict(entry.options) if entry.options else {}

    def _format_time(self, hour: int) -> str:
        hour = max(0, min(23, int(hour)))
        return f"{hour:02d}:00"

    def _parse_hour(self, value) -> int:
        try:
            if isinstance(value, str) and ":" in value:
                parts = value.split(":")
                return max(0, min(23, int(parts[0])))
            return max(0, min(23, int(value)))
        except Exception:
            return DEFAULT_UPDATE_HOUR

    async def async_step_init(self, user_input=None):
        return await self.async_step_choice(user_input)

    async def async_step_choice(self, user_input=None):
        if user_input is not None:
            install_skyfield = bool(user_input.get("install_skyfield", True))
            num_events = int(user_input.get("num_events", DEFAULT_NUM_EVENTS))
            update_hour = self._parse_hour(user_input.get("update_hour", DEFAULT_UPDATE_HOUR))
            min_coverage = int(user_input.get("min_coverage", self._opts.get("min_coverage", DEFAULT_MIN_COVERAGE)))
            min_coverage = max(0, min(100, min_coverage))
            self._opts["install_skyfield"] = install_skyfield
            self._opts["num_events"] = max(1, min(10, num_events))
            self._opts["update_hour"] = update_hour
            self._opts["min_coverage"] = min_coverage
            if install_skyfield:
                return await self.async_step_coords()
            return await self.async_step_region()

        current = self._opts.get("install_skyfield", self.entry.data.get("install_skyfield", True))
        num_events = int(self._opts.get("num_events", self.entry.data.get("num_events", DEFAULT_NUM_EVENTS)))
        update_hour = int(self._opts.get("update_hour", self.entry.data.get("update_hour", DEFAULT_UPDATE_HOUR)))
        min_coverage = int(self._opts.get("min_coverage", self.entry.data.get("min_coverage", DEFAULT_MIN_COVERAGE)))
        schema = vol.Schema({
            vol.Required("install_skyfield", default=bool(current)): bool,
            vol.Required("num_events", default=num_events): int,
            vol.Required("update_hour", default=self._format_time(update_hour)): str,
            vol.Required("min_coverage", default=min_coverage): int,
        })
        return self.async_show_form(step_id="choice", data_schema=schema)

    async def async_step_coords(self, user_input=None):
        if user_input is not None:
            self._opts.update(user_input)
            return self.async_create_entry(title="Options", data=self._opts)

        lat = (
            self._opts.get("latitude")
            or self.entry.data.get("latitude")
            or (self.hass.config.latitude or 0.0)
        )
        lon = (
            self._opts.get("longitude")
            or self.entry.data.get("longitude")
            or (self.hass.config.longitude or 0.0)
        )
        schema = vol.Schema({
            vol.Required("latitude", default=float(lat)): float,
            vol.Required("longitude", default=float(lon)): float,
        })
        return self.async_show_form(step_id="coords", data_schema=schema)

    async def async_step_region(self, user_input=None):
        if user_input is not None:
            self._opts.update(user_input)
            return self.async_create_entry(title="Options", data=self._opts)

        region_default = self._opts.get("region") or self.entry.data.get("region") or "Europe"
        schema = vol.Schema({
            vol.Required("region", default=region_default): vol.In(SUPPORTED_REGIONS),
        })
        return self.async_show_form(step_id="region", data_schema=schema)
