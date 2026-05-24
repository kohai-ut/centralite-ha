"""Centralite Lighting integration for Home Assistant.

Supports Centralite Elegance and JetStream lighting systems over RS-232.

Home Assistant imports are deferred to function bodies so the package
loads (and the protocol submodules are importable) without homeassistant
installed — useful for running protocol unit tests in a minimal Python env.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Plain strings so this constant doesn't require importing the Platform enum
# at module load (which would force a homeassistant import).
PLATFORMS: list[str] = ["light", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Centralite from a config entry."""
    from homeassistant.exceptions import ConfigEntryNotReady

    from .const import (
        CONF_BAUD,
        CONF_PORT,
        CONF_SYSTEM_TYPE,
        DEFAULT_BAUD,
        DOMAIN,
        SYSTEM_ELEGANCE,
        SYSTEM_JETSTREAM,
    )
    from .coordinator import CentraliteCoordinator
    from .protocol.elegance import EleganceProtocol
    from .protocol.jetstream import JetStreamProtocol

    system_type = entry.data[CONF_SYSTEM_TYPE]
    port = entry.data[CONF_PORT]
    baud = entry.data.get(CONF_BAUD, DEFAULT_BAUD)

    if system_type == SYSTEM_ELEGANCE:
        protocol = EleganceProtocol(port, baudrate=baud)
    elif system_type == SYSTEM_JETSTREAM:
        protocol = JetStreamProtocol(port, baudrate=baud)
    else:
        _LOGGER.error("Unknown system_type %r", system_type)
        return False

    coordinator = CentraliteCoordinator(hass, entry, protocol)
    try:
        await coordinator.async_init()
    except Exception as e:
        raise ConfigEntryNotReady(f"Failed to connect to Centralite bridge: {e}") from e

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    from .const import DOMAIN
    from .coordinator import CentraliteCoordinator

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: CentraliteCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change so renames/poll-interval take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
