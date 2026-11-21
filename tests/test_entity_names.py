"""Entity names that the import did not supply come from the translation file.

The English text is unchanged, so entity IDs and displayed names are the same
as before; only where the text lives has moved.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite.const import (
    CONF_BUTTON_IDS,
    CONF_LOAD_IDS,
    CONF_SCENE_IDS,
    CONF_SWITCH_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
)

from .conftest import FakeProtocol

_JS = "custom_components.centralite.protocol.jetstream.JetStreamProtocol"
_ELG = "custom_components.centralite.protocol.elegance.EleganceProtocol"


def _rows(hass, entry):
    reg = er.async_get(hass)
    return {
        row.unique_id.removeprefix(f"{entry.entry_id}_"): row
        for row in er.async_entries_for_config_entry(reg, entry.entry_id)
    }


async def test_elegance_fallback_names_are_translated_and_unchanged(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SYSTEM_TYPE: SYSTEM_ELEGANCE,
            "port": "/dev/ttyUSB0",
            "baud": 19200,
            CONF_LOAD_IDS: [1, 2],
            CONF_SWITCH_IDS: [5],
            CONF_SCENE_IDS: [3],
        },
        options={OPT_LOAD_NAMES: {"2": "Hall"}},
        title="Bridge",
        unique_id="elegance@/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)
    with patch(_ELG, return_value=FakeProtocol(bulk_loads={1: True}, supports_clock=True)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    rows = _rows(hass, entry)
    expected = {
        "load_001": ("load", "Load 001", "light.bridge_load_001"),
        "switch_005": ("keypad_switch", "Switch 005", "switch.bridge_switch_005"),
        "scene_003": ("scene", "Scene 003", "switch.bridge_scene_003"),
        "sync_clock": ("sync_clock", "Sync Clock", "button.bridge_sync_clock"),
    }
    for suffix, (key, name, entity_id) in expected.items():
        row = rows[suffix]
        assert row.translation_key == key, suffix
        assert row.original_name == name, suffix
        assert row.entity_id == entity_id, suffix
    # An imported name is used verbatim and is not a translation.
    assert rows["load_002"].original_name == "Hall"
    assert rows["load_002"].translation_key is None


async def test_jetstream_button_switch_fallback_name(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SYSTEM_TYPE: SYSTEM_JETSTREAM,
            "port": "/dev/ttyUSB0",
            "baud": 19200,
            CONF_BUTTON_IDS: [[44, 2]],
        },
        title="JS",
        unique_id="jetstream@/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)
    with patch(_JS, return_value=FakeProtocol(supports_bulk=False)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    row = _rows(hass, entry)["button_044_02"]
    assert row.translation_key == "button_switch"
    assert row.original_name == "Switch 044 Button 2"
    assert row.entity_id == "switch.js_switch_044_button_2"
