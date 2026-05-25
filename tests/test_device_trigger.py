"""Device-trigger tests: enumeration, event firing, and end-to-end automation."""

from __future__ import annotations

from homeassistant.const import CONF_DEVICE_ID, CONF_TYPE
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite import device_trigger
from custom_components.centralite.const import (
    CONF_BUTTON_IDS,
    CONF_LOAD_IDS,
    CONF_SUBTYPE,
    CONF_SWITCH_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    EVENT_BUTTON,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
    button_subtype,
)
from custom_components.centralite.coordinator import CentraliteCoordinator
from custom_components.centralite.protocol import SwitchEvent

from .conftest import FakeProtocol


def _entry_and_device(hass, system, data):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SYSTEM_TYPE: system, "port": "/dev/ttyUSB0", "baud": 19200, **data},
        title="Bridge",
    )
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    return entry, device


async def test_get_triggers_jetstream(hass):
    """Each known device offers all 3 buttons (so .jts-imported devices work,
    and buttons 2/3 are reachable — not just the configured button list)."""
    _entry, device = _entry_and_device(
        hass, SYSTEM_JETSTREAM, {CONF_LOAD_IDS: [44]}
    )
    triggers = await device_trigger.async_get_triggers(hass, device.id)
    # 3 buttons x 3 actions
    assert len(triggers) == 9
    subtypes = {t[CONF_SUBTYPE] for t in triggers}
    assert subtypes == {button_subtype(44, b) for b in (1, 2, 3)}
    assert {t[CONF_TYPE] for t in triggers} == {"tap", "press", "release"}


async def test_get_triggers_includes_button_only_devices(hass):
    """A device known only via a button switch (not a load) still gets triggers."""
    _entry, device = _entry_and_device(
        hass, SYSTEM_JETSTREAM, {CONF_LOAD_IDS: [44], CONF_BUTTON_IDS: [[50, 1]]}
    )
    triggers = await device_trigger.async_get_triggers(hass, device.id)
    subtypes = {t[CONF_SUBTYPE] for t in triggers}
    # both device 44 (load) and device 50 (button-only) get buttons 1-3
    assert button_subtype(50, 3) in subtypes
    assert button_subtype(44, 1) in subtypes


async def test_get_triggers_elegance_no_tap(hass):
    _entry, device = _entry_and_device(
        hass, SYSTEM_ELEGANCE, {CONF_SWITCH_IDS: [53]}
    )
    triggers = await device_trigger.async_get_triggers(hass, device.id)
    assert {t[CONF_TYPE] for t in triggers} == {"press", "release"}  # no tap
    assert all(t[CONF_SUBTYPE] == button_subtype(53, 0) for t in triggers)


async def test_coordinator_fires_button_event(hass):
    entry, device = _entry_and_device(
        hass, SYSTEM_JETSTREAM, {CONF_BUTTON_IDS: [[44, 1]]}
    )
    coord = CentraliteCoordinator(hass, entry, FakeProtocol(bulk_loads={}))
    events = []
    hass.bus.async_listen(EVENT_BUTTON, lambda e: events.append(e.data))
    coord.protocol.switch_cb(SwitchEvent(idx=44, action="tap", button=1))
    await hass.async_block_till_done()
    assert events == [
        {
            CONF_DEVICE_ID: device.id,
            CONF_TYPE: "tap",
            CONF_SUBTYPE: button_subtype(44, 1),
        }
    ]


async def test_device_trigger_fires_automation(hass):
    """End-to-end: a physical button event runs an automation via the device trigger."""
    entry, device = _entry_and_device(
        hass, SYSTEM_JETSTREAM, {CONF_BUTTON_IDS: [[44, 2]]}
    )
    fired = []
    hass.bus.async_listen("centralite_test_fired", lambda e: fired.append(e))

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "trigger": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": device.id,
                    "type": "press",
                    "subtype": button_subtype(44, 2),
                },
                "action": {"event": "centralite_test_fired"},
            }
        },
    )
    await hass.async_block_till_done()

    # A matching button event fires the automation...
    hass.bus.async_fire(
        EVENT_BUTTON,
        {
            CONF_DEVICE_ID: device.id,
            CONF_TYPE: "press",
            CONF_SUBTYPE: button_subtype(44, 2),
        },
    )
    await hass.async_block_till_done()
    assert len(fired) == 1

    # ...but a different action on the same button does not.
    hass.bus.async_fire(
        EVENT_BUTTON,
        {
            CONF_DEVICE_ID: device.id,
            CONF_TYPE: "release",
            CONF_SUBTYPE: button_subtype(44, 2),
        },
    )
    await hass.async_block_till_done()
    assert len(fired) == 1
