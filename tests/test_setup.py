"""Setup/unload tests with a patched protocol (no real serial).

Covers async_setup_entry -> coordinator stored + platforms forwarded,
async_unload_entry -> coordinator disconnected + popped, and the
ConfigEntryNotReady path when the bridge connection fails.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite.const import (
    CONF_LOAD_IDS,
    CONF_SCENE_IDS,
    CONF_SWITCH_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    SYSTEM_ELEGANCE,
)

from .conftest import FakeProtocol

_PROTO_PATH = "custom_components.centralite.protocol.elegance.EleganceProtocol"


def _entry(hass):
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
        title="Bridge",
        unique_id="elegance@/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_and_unload(hass):
    entry = _entry(hass)
    proto = FakeProtocol(bulk_loads={1: True})
    with patch(_PROTO_PATH, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id in hass.data[DOMAIN]
    assert proto.connected is True
    # Platforms forwarded: light (loads) + switch (switch + scene) entities exist.
    assert hass.states.get("light.bridge_load_001") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert entry.entry_id not in hass.data[DOMAIN]
    assert proto.connected is False  # disconnect() ran


async def test_setup_retry_when_connection_fails(hass):
    entry = _entry(hass)
    proto = FakeProtocol(connect_error=OSError("no such device"))
    with patch(_PROTO_PATH, return_value=proto):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
