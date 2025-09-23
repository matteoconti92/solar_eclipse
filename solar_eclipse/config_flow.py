import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN, SUPPORTED_REGIONS

class SolarEclipseConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Solar Eclipse integration."""

    VERSION = 2

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Solar Eclipse", data=user_input)

        # Defaults from HA config
        lat = self.hass.config.latitude or 0.0
        lon = self.hass.config.longitude or 0.0
        region_default = "Europe"

        schema = vol.Schema({
            vol.Required("install_skyfield", default=True): bool,
            vol.Required("latitude", default=lat): float,
            vol.Required("longitude", default=lon): float,
            vol.Required("region", default=region_default): vol.In(SUPPORTED_REGIONS),
        })
        return self.async_show_form(step_id="user", data_schema=schema)
