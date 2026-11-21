"""Keypad button entities on the JetStream per-device layout.

Each configured button gets an event entity (presses at the wall), a button
entity that taps it from Home Assistant, and an optional hold button. The old
button switches exist only on the original single-bridge layout.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.components.event import ATTR_EVENT_TYPE, ATTR_EVENT_TYPES
from homeassistant.components.event.const import ButtonEventType
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite.const import (
    CONF_BUTTON_IDS,
    CONF_LOAD_IDS,
    CONF_SWITCH_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
)
from custom_components.centralite.event import (
    EVENT_LONG_PRESS_END,
    EVENT_LONG_PRESS_START,
    EVENT_SINGLE_PRESS,
)
from custom_components.centralite.jetstream_devices import (
    CONF_TOPOLOGY,
    TOPOLOGY_PER_DEVICE,
    unit_identifier,
)
from custom_components.centralite.protocol import SwitchEvent

from .conftest import FakeProtocol

_JS = "custom_components.centralite.protocol.jetstream.JetStreamProtocol"
_ELG = "custom_components.centralite.protocol.elegance.EleganceProtocol"


def _entry(hass, *, per_device=True):
    data = {
        CONF_SYSTEM_TYPE: SYSTEM_JETSTREAM,
        "port": "/dev/ttyUSB0",
        "baud": 19200,
        CONF_LOAD_IDS: [55],
        CONF_BUTTON_IDS: [[55, 1], [55, 3], [60, 2]],
    }
    if per_device:
        data[CONF_TOPOLOGY] = TOPOLOGY_PER_DEVICE
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options={OPT_LOAD_NAMES: {"55": "Family SW"}},
        title="JS",
        unique_id="jetstream@/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry, proto=None):
    proto = proto or FakeProtocol(supports_bulk=False, supports_scene_push=True)
    with patch(_JS, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return proto


def _row(hass, entry, domain, suffix):
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{suffix}")
    return reg.async_get(eid) if eid else None


def _unit(hass, entry, idx):
    return dr.async_get(hass).async_get_device_by_identifier(
        unit_identifier(entry.entry_id, idx), entry.entry_id
    )


# --- Event entity ----------------------------------------------------------


async def test_event_entity_per_configured_button_on_its_unit(hass):
    entry = _entry(hass)
    await _setup(hass, entry)

    for dev, btn in ((55, 1), (55, 3), (60, 2)):
        row = _row(hass, entry, "event", f"button_event_{dev:03d}_{btn:02d}")
        assert row is not None, (dev, btn)
        assert row.device_id == _unit(hass, entry, dev).id
        assert row.original_name == f"Button {btn}"
    assert _row(hass, entry, "event", "button_event_055_02") is None

    state = hass.states.get(_row(hass, entry, "event", "button_event_055_03").entity_id)
    assert state.attributes["device_class"] == "button"
    assert state.attributes[ATTR_EVENT_TYPES] == [
        EVENT_SINGLE_PRESS,
        EVENT_LONG_PRESS_START,
        EVENT_LONG_PRESS_END,
    ]
    assert state.attributes["friendly_name"] == "Family SW Button 3"


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("tap", EVENT_SINGLE_PRESS),
        ("press", EVENT_LONG_PRESS_START),
        ("release", EVENT_LONG_PRESS_END),
    ],
)
async def test_bridge_report_maps_to_event_type(hass, action, expected):
    entry = _entry(hass)
    proto = await _setup(hass, entry)
    pressed = _row(hass, entry, "event", "button_event_055_03").entity_id
    other = _row(hass, entry, "event", "button_event_055_01").entity_id

    proto.switch_cb(SwitchEvent(idx=55, button=3, action=action))
    await hass.async_block_till_done()

    assert hass.states.get(pressed).attributes[ATTR_EVENT_TYPE] == expected
    assert hass.states.get(other).attributes[ATTR_EVENT_TYPE] is None


async def test_double_tap_is_two_single_presses(hass):
    entry = _entry(hass)
    proto = await _setup(hass, entry)
    entity_id = _row(hass, entry, "event", "button_event_055_03").entity_id
    seen = []

    @callback
    def _record(event):
        if event.data["entity_id"] == entity_id:
            seen.append(event.data["new_state"].attributes[ATTR_EVENT_TYPE])

    hass.bus.async_listen("state_changed", _record)
    proto.switch_cb(SwitchEvent(idx=55, button=3, action="tap"))
    await hass.async_block_till_done()
    proto.switch_cb(SwitchEvent(idx=55, button=3, action="tap"))
    await hass.async_block_till_done()

    assert seen == [EVENT_SINGLE_PRESS, EVENT_SINGLE_PRESS]


def test_event_names_match_home_assistant_standard_types():
    """Our literal names must be Home Assistant's standard button event types.

    `single_press` first ships in Home Assistant 2026.12; on older test
    harnesses only the long-press pair can be checked.
    """
    assert ButtonEventType.LONG_PRESS_START == EVENT_LONG_PRESS_START
    assert ButtonEventType.LONG_PRESS_END == EVENT_LONG_PRESS_END
    if hasattr(ButtonEventType, "SINGLE_PRESS"):
        assert ButtonEventType.SINGLE_PRESS == EVENT_SINGLE_PRESS


# --- Tap button ------------------------------------------------------------


async def test_tap_button_sends_a_tap(hass):
    entry = _entry(hass)
    proto = await _setup(hass, entry)
    row = _row(hass, entry, "button", "button_press_055_03")
    assert row.original_name == "Tap button 3"
    assert row.device_id == _unit(hass, entry, 55).id

    await hass.services.async_call(
        "button", "press", {"entity_id": row.entity_id}, blocking=True
    )
    assert ("tap_switch", 55, 3) in proto.calls


# --- Hold button -----------------------------------------------------------


async def _enabled_hold(hass, entry, proto):
    """Set up, enable the hold button for 055/03, reload; return its entity id."""
    await _setup(hass, entry, proto)
    row = _row(hass, entry, "button", "button_hold_055_03")
    assert row.original_name == "Hold button 3"
    assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    er.async_get(hass).async_update_entity(row.entity_id, disabled_by=None)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await _setup(hass, entry, proto)
    return row.entity_id


async def test_hold_button_is_off_by_default_and_presses_then_releases(hass):
    entry = _entry(hass)
    proto = FakeProtocol(supports_bulk=False, supports_scene_push=True)
    entity_id = await _enabled_hold(hass, entry, proto)
    proto.calls.clear()

    with patch("custom_components.centralite.button.BUTTON_HOLD_SECONDS", 0):
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )

    assert proto.calls == [("press_switch", 55, 3), ("release_switch", 55, 3)]


async def test_hold_button_retries_the_release_once(hass):
    """A button left pressed keeps ramping the load, so the release is retried."""
    entry = _entry(hass)
    proto = FakeProtocol(supports_bulk=False, supports_scene_push=True)
    entity_id = await _enabled_hold(hass, entry, proto)
    proto.calls.clear()
    proto.fail_release_once = True

    with patch("custom_components.centralite.button.BUTTON_HOLD_SECONDS", 0):
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )

    assert proto.calls == [
        ("press_switch", 55, 3),
        ("release_switch", 55, 3),
        ("release_switch", 55, 3),
    ]


# --- Which layout gets which entities --------------------------------------


async def test_per_device_layout_has_no_button_switches(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    assert _row(hass, entry, "switch", "button_055_03") is None


async def test_old_layout_keeps_switches_and_gets_no_new_entities(hass):
    entry = _entry(hass, per_device=False)
    await _setup(hass, entry)
    assert _row(hass, entry, "switch", "button_055_03") is not None
    assert _row(hass, entry, "event", "button_event_055_03") is None
    assert _row(hass, entry, "button", "button_press_055_03") is None
    assert _row(hass, entry, "button", "button_hold_055_03") is None


async def test_moving_to_per_device_removes_the_old_button_switches(hass):
    entry = _entry(hass, per_device=False)
    await _setup(hass, entry)
    light = _row(hass, entry, "light", "load_055")
    assert _row(hass, entry, "switch", "button_055_03") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TOPOLOGY: TOPOLOGY_PER_DEVICE}
    )
    await _setup(hass, entry)

    for suffix in ("button_055_01", "button_055_03", "button_060_02"):
        assert _row(hass, entry, "switch", suffix) is None, suffix
    assert _row(hass, entry, "event", "button_event_055_03") is not None
    # Lights are untouched by the cleanup.
    assert _row(hass, entry, "light", "load_055").id == light.id


async def test_elegance_gets_no_keypad_button_entities(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SYSTEM_TYPE: SYSTEM_ELEGANCE,
            "port": "/dev/ttyUSB1",
            "baud": 19200,
            CONF_LOAD_IDS: [1],
            CONF_SWITCH_IDS: [5],
            CONF_TOPOLOGY: TOPOLOGY_PER_DEVICE,
        },
        title="ELG",
        unique_id="elegance@/dev/ttyUSB1",
    )
    entry.add_to_hass(hass)
    with patch(_ELG, return_value=FakeProtocol(bulk_loads={1: True})):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    reg = er.async_get(hass)
    domains = {r.domain for r in er.async_entries_for_config_entry(reg, entry.entry_id)}
    assert "event" not in domains
    assert _row(hass, entry, "switch", "switch_005") is not None
