"""Home Assistant integration: Solar Eclipse."""
from .const import DOMAIN
import os
import shutil

async def async_setup(hass, config):
    """Set up the integration."""
    return True

async def _update_listener(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_setup_entry(hass, entry):
    """Set up from a config entry."""
    # Forward setup to sensor and binary_sensor platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "binary_sensor"])
    # Register options update listener once per entry
    entry.async_on_unload(entry.add_update_listener(_update_listener))
    return True

async def async_unload_entry(hass, entry):
    """Unload config entry."""
    ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "binary_sensor"])
    # Cleanup skyfield cache dir
    skyfield_dir = hass.config.path(".storage/solar_eclipse_skyfield")
    try:
        if os.path.isdir(skyfield_dir):
            shutil.rmtree(skyfield_dir, ignore_errors=True)
    except Exception:
        pass
    return ok
