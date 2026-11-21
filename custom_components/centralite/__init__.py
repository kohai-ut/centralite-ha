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
PLATFORMS: list[str] = ["button", "event", "light", "switch"]


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

    # One-time migration of v1 unique_ids (idempotent; safe on every load).
    from .migrate import async_migrate_entries

    await async_migrate_entries(hass, entry)

    coordinator = CentraliteCoordinator(hass, entry, protocol)

    # Register the bridge itself rather than leaving it to whichever entity
    # loads first: unit devices link to it, button events name it, and a
    # JetStream bridge may have no entities of its own.
    from homeassistant.helpers import device_registry as dr

    from .const import MANUFACTURER

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=coordinator.device_name,
        manufacturer=MANUFACTURER,
        model=coordinator.system_label,
    )

    _remove_button_switches_on_per_device(hass, entry)

    try:
        await coordinator.async_init()
    except Exception as e:
        raise ConfigEntryNotReady(f"Failed to connect to Centralite bridge: {e}") from e

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


def _remove_button_switches_on_per_device(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Drop the old JetStream button switches once an entry is per-device.

    An entry has the button switches (original layout) or the event and
    button entities (per-device layout), never both. Moving to per-device is
    the user's explicit choice, made after a preview that lists these
    switches; this only carries it out, and does nothing on later loads.
    """
    from homeassistant.helpers import entity_registry as er

    from .const import DOMAIN
    from .jetstream_devices import is_per_device

    if not is_per_device(entry.data):
        return
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_button_"
    for row in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            row.domain == "switch"
            and row.platform == DOMAIN
            and row.unique_id.startswith(prefix)
        ):
            _LOGGER.info("Removing retired button switch %s", row.entity_id)
            registry.async_remove(row.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    from .const import DOMAIN
    from .coordinator import CentraliteCoordinator

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: CentraliteCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear the duplicate-Device-ID notice raised at import, if any.

    The notice is tied to this bridge's import; once the entry is gone (for
    example to re-import a corrected .jts) it no longer describes anything.
    """
    from homeassistant.helpers import issue_registry as ir

    from .const import DOMAIN

    ir.async_delete_issue(hass, DOMAIN, f"jts_duplicate_ids_{entry.unique_id}")


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change so renames/poll-interval take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
