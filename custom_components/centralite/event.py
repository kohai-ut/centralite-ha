"""Event platform: physical JetStream keypad button presses.

One event entity per configured button, on the per-device layout only. It
records each press reported by the bridge; it has no on/off state and cannot
be operated. To press a button from Home Assistant use the button platform.

The bridge reports an already-classified press, so each report maps to exactly
one standard Home Assistant button event type (measured on hardware):

- ``T`` (tap)      a short press and release            -> single_press
- ``P`` (press)    sent only once a press is a hold     -> long_press_start
- ``R`` (release)  sent on release after a hold         -> long_press_end

A double tap arrives as two taps and is passed through as two single presses;
nothing is combined or timed here. Taps sent from Home Assistant produce no
report, so this entity fires only for presses at the wall.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import callback

from .const import CONF_BUTTON_IDS, DOMAIN
from .entity import CentraliteBaseEntity
from .jetstream_devices import is_per_device

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import CentraliteCoordinator
    from .protocol import SwitchEvent

# Home Assistant's standard button event types, as literals: `single_press`
# first ships in Home Assistant 2026.12, and a test checks these against
# ButtonEventType wherever the running version defines them.
EVENT_SINGLE_PRESS: Final = "single_press"
EVENT_LONG_PRESS_START: Final = "long_press_start"
EVENT_LONG_PRESS_END: Final = "long_press_end"

_ACTION_TO_EVENT: Final = {
    "tap": EVENT_SINGLE_PRESS,
    "press": EVENT_LONG_PRESS_START,
    "release": EVENT_LONG_PRESS_END,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if not is_per_device(entry.data):
        return
    coordinator: CentraliteCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        CentraliteButtonEvent(coordinator, pair[0], pair[1])
        for pair in entry.data.get(CONF_BUTTON_IDS, [])
    )


class CentraliteButtonEvent(CentraliteBaseEntity, EventEntity):
    """One physical button on a JetStream unit."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [  # noqa: RUF012 - Home Assistant entity attribute
        EVENT_SINGLE_PRESS,
        EVENT_LONG_PRESS_START,
        EVENT_LONG_PRESS_END,
    ]
    _attr_translation_key = "keypad_button"

    def __init__(
        self, coordinator: CentraliteCoordinator, device_idx: int, button_idx: int
    ) -> None:
        super().__init__(coordinator, unit_idx=device_idx)
        self._key = (device_idx, button_idx)
        self._attr_unique_id = (
            f"{self._entry_id}_button_event_{device_idx:03d}_{button_idx:02d}"
        )
        self._attr_translation_placeholders = {"number": str(button_idx)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_button_listener(self._on_button)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Ignore coordinator refreshes: only a button report changes this entity."""

    @callback
    def _on_button(self, event: SwitchEvent) -> None:
        if (event.idx, event.button) != self._key:
            return
        event_type = _ACTION_TO_EVENT.get(event.action)
        if event_type is None:
            return
        self._trigger_event(event_type)
        self.async_write_ha_state()
