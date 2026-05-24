"""Config flow for Centralite."""

from homeassistant import config_entries

from .const import DOMAIN


class CentraliteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Centralite."""

    VERSION = 1
