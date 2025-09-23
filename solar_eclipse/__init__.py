"""Home Assistant integration: Solar Eclipse."""
from .const import DOMAIN

async def async_setup(hass, config):
    """Set up the integration."""
    return True

async def async_setup_entry(hass, entry):
    """Set up from a config entry."""
    # Forward setup to sensor and binary_sensor platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "binary_sensor"])
    return True

async def async_unload_entry(hass, entry):
    """Unload config entry."""
    return await hass.config_entries.async_unload_platforms(entry, ["sensor", "binary_sensor"])
