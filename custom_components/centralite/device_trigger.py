"""Device triggers for physical Centralite button presses.

JetStream keypad buttons emit ACT (tap/press/release) events, and Elegance
switches emit press/release. The coordinator turns each into an EVENT_BUTTON bus
event (see coordinator._fire_button_event). This module exposes those as Home
Assistant device triggers so automations can run on a physical button press.

Triggers are enumerated from the configured buttons/switches (JetStream
CONF_BUTTON_IDS, Elegance CONF_SWITCH_IDS). Automations can also listen for the
raw ``centralite_event`` directly if a button isn't enumerated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_EVENT_DATA,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_SUBTYPE,
    CONF_SWITCH_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    EVENT_BUTTON,
    SYSTEM_JETSTREAM,
    button_subtype,
)
from .jetstream_devices import parse_unit_identifier, unit_addresses
from .protocol.jetstream import JETSTREAM_MAX_BUTTONS_PER_SWITCH

if TYPE_CHECKING:
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant
    from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
    from homeassistant.helpers.typing import ConfigType

# Actions a button can report. JetStream emits all three; Elegance press/release.
TRIGGER_TYPES = ("tap", "press", "release")

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Required(CONF_SUBTYPE): cv.string,
    }
)


def _trigger(device_id: str, action: str, subtype: str) -> dict:
    return {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: device_id,
        CONF_TYPE: action,
        CONF_SUBTYPE: subtype,
    }


def _unit_address(device: dr.DeviceEntry, entry_id: str) -> int | None:
    """JetStream unit address if this is one of the entry's unit devices."""
    for identifier in device.identifiers:
        if len(identifier) == 2:
            idx = parse_unit_identifier(entry_id, identifier)
            if idx is not None:
                return idx
    return None


def _is_bridge(device: dr.DeviceEntry, entry_id: str) -> bool:
    return (DOMAIN, entry_id) in device.identifiers


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """List the button triggers a Centralite device offers.

    The bridge offers every button it knows about (as it always has). A
    JetStream unit device offers only its own three buttons.
    """
    device = dr.async_get(hass).async_get(device_id)
    if device is None or device.config_entry_id is None:
        return []
    entry = hass.config_entries.async_get_entry(device.config_entry_id)
    if entry is None or entry.domain != DOMAIN:
        return []

    triggers: list[dict[str, str]] = []
    if entry.data.get(CONF_SYSTEM_TYPE) == SYSTEM_JETSTREAM:
        # Every JetStream device can host up to 3 keypad buttons (the protocol
        # numbers them 1-3, exactly what an ACT event reports). Offer all three
        # per known device so any physical button is trigger-able. Devices come
        # from the loads plus any device that has a configured button
        # (covers a keypad-only device entered manually, not just .jts loads).
        known = unit_addresses(entry.data)
        unit = _unit_address(device, entry.entry_id)
        if unit is not None:
            device_ids = [unit] if unit in known else []
        elif _is_bridge(device, entry.entry_id):
            device_ids = known
        else:
            device_ids = []
        for device_idx in device_ids:
            for button in range(1, JETSTREAM_MAX_BUTTONS_PER_SWITCH + 1):
                subtype = button_subtype(device_idx, button)
                triggers += [_trigger(device_id, t, subtype) for t in TRIGGER_TYPES]
    else:
        # Elegance switches have no separate button index (button 0) and never
        # report a "tap".
        for idx in entry.data.get(CONF_SWITCH_IDS, []):
            subtype = button_subtype(idx, 0)
            triggers += [_trigger(device_id, t, subtype) for t in ("press", "release")]
    return triggers


def _event_device_id(hass: HomeAssistant, device_id: str) -> str:
    """The device id a button event will carry for a trigger on ``device_id``.

    Button events always name the bridge. A trigger saved against a JetStream
    unit device therefore has to listen for its bridge's id; the subtype still
    pins it to that unit's button.
    """
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None or device.config_entry_id is None:
        return device_id
    entry_id = device.config_entry_id
    if _unit_address(device, entry_id) is None:
        return device_id
    bridge = registry.async_get_device_by_identifier((DOMAIN, entry_id), entry_id)
    return bridge.id if bridge else device_id


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a device trigger: fire when the matching EVENT_BUTTON arrives."""
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_BUTTON,
            CONF_EVENT_DATA: {
                CONF_DEVICE_ID: _event_device_id(hass, config[CONF_DEVICE_ID]),
                CONF_TYPE: config[CONF_TYPE],
                CONF_SUBTYPE: config[CONF_SUBTYPE],
            },
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
