"""Button platform for the Centralite integration.

Hosts a single "Sync Clock" button for bridges with a settable real-time
clock (Elegance, via ^K/^L — JetStream has no clock command). Pressing it
writes Home Assistant's current local time to the bridge RTC, so the bridge's
time-of-day schedules stay aligned with HA. Automate the press (e.g. nightly)
to correct clock drift without a trip to the panel.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import CentraliteBaseEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import CentraliteCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CentraliteCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.protocol.supports_clock:
        async_add_entities([CentraliteSyncClockButton(coordinator)])


class CentraliteSyncClockButton(CentraliteBaseEntity, ButtonEntity):
    """Set the bridge's real-time clock to Home Assistant's current time."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: CentraliteCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._entry_id}_sync_clock"
        self._attr_name = "Sync Clock"

    async def async_press(self) -> None:
        # The bridge RTC tracks wall-clock local time, and encode_bcd_clock
        # reads the naive local fields (hour/day/weekday/...). dt_util.now()
        # is tz-aware in HA's configured zone, so its .hour etc. are local.
        now = dt_util.now()
        _LOGGER.debug("Syncing Centralite bridge clock to %s", now.isoformat())
        await self.coordinator.protocol.set_clock(now)
