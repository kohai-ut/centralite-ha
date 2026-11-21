"""Button platform for the Centralite integration.

Hosts a single "Sync Clock" button for bridges with a settable real-time
clock (Elegance, via ^K/^L — JetStream has no clock command). Pressing it
writes Home Assistant's current local time to the bridge RTC, so the bridge's
time-of-day schedules stay aligned with HA. Automate the press (e.g. nightly)
to correct clock drift without a trip to the panel.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

from .const import BUTTON_HOLD_SECONDS, CONF_BUTTON_IDS, DOMAIN
from .entity import CentraliteBaseEntity
from .jetstream_devices import is_per_device
from .protocol.common import ProtocolError

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
    if is_per_device(entry.data):
        entities: list[ButtonEntity] = []
        for pair in entry.data.get(CONF_BUTTON_IDS, []):
            entities.append(CentraliteTapButton(coordinator, pair[0], pair[1]))
            entities.append(CentraliteHoldButton(coordinator, pair[0], pair[1]))
        async_add_entities(entities)


class CentraliteSyncClockButton(CentraliteBaseEntity, ButtonEntity):
    """Set the bridge's real-time clock to Home Assistant's current time."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: CentraliteCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._entry_id}_sync_clock"
        self._attr_translation_key = "sync_clock"

    async def async_press(self) -> None:
        # The bridge RTC tracks wall-clock local time, and encode_bcd_clock
        # reads the naive local fields (hour/day/weekday/...). dt_util.now()
        # is tz-aware in HA's configured zone, so its .hour etc. are local.
        now = dt_util.now()
        _LOGGER.debug("Syncing Centralite bridge clock to %s", now.isoformat())
        await self.coordinator.protocol.set_clock(now)


class CentraliteTapButton(CentraliteBaseEntity, ButtonEntity):
    """Tap one JetStream keypad button from Home Assistant.

    JetStream then does whatever that button is programmed to do. Presses at
    the wall are reported by the matching event entity, not here.
    """

    _attr_translation_key = "tap_button"

    def __init__(
        self, coordinator: CentraliteCoordinator, device_idx: int, button_idx: int
    ) -> None:
        super().__init__(coordinator, unit_idx=device_idx)
        self._device_idx = device_idx
        self._button_idx = button_idx
        self._attr_unique_id = (
            f"{self._entry_id}_button_press_{device_idx:03d}_{button_idx:02d}"
        )
        self._attr_translation_placeholders = {"number": str(button_idx)}

    async def async_press(self) -> None:
        await self.coordinator.protocol.tap_switch(
            self._device_idx, button=self._button_idx
        )


class CentraliteHoldButton(CentraliteBaseEntity, ButtonEntity):
    """Hold one JetStream keypad button: press, wait, release.

    Runs the button's programmed press-and-hold action. Off by default: on a
    button whose hold just dims its own light, the light entity does that
    better.
    """

    _attr_translation_key = "hold_button"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: CentraliteCoordinator, device_idx: int, button_idx: int
    ) -> None:
        super().__init__(coordinator, unit_idx=device_idx)
        self._device_idx = device_idx
        self._button_idx = button_idx
        self._attr_unique_id = (
            f"{self._entry_id}_button_hold_{device_idx:03d}_{button_idx:02d}"
        )
        self._attr_translation_placeholders = {"number": str(button_idx)}

    async def async_press(self) -> None:
        protocol = self.coordinator.protocol
        dev, btn = self._device_idx, self._button_idx
        await protocol.press_switch(dev, button=btn, auto_release=False)
        try:
            await asyncio.sleep(BUTTON_HOLD_SECONDS)
        finally:
            # A button left pressed keeps ramping its load, so the release is
            # sent even if the wait is cancelled, and retried once if it fails.
            try:
                await protocol.release_switch(dev, button=btn)
            except (ProtocolError, OSError) as err:
                _LOGGER.warning(
                    "Release of button %03d/%d failed (%s); retrying once",
                    dev,
                    btn,
                    err,
                )
                await protocol.release_switch(dev, button=btn)
