"""Delete on a JetStream unit's device page removes it from the integration."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
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
from custom_components.centralite.jetstream_devices import (
    CONF_TOPOLOGY,
    TOPOLOGY_PER_DEVICE,
    unit_identifier,
)

from .conftest import FakeProtocol

_JS = "custom_components.centralite.protocol.jetstream.JetStreamProtocol"
_ELG = "custom_components.centralite.protocol.elegance.EleganceProtocol"


def _jetstream_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SYSTEM_TYPE: SYSTEM_JETSTREAM,
            "port": "/dev/ttyUSB0",
            "baud": 19200,
            CONF_LOAD_IDS: [1, 2],
            CONF_BUTTON_IDS: [[1, 1], [2, 1]],
            CONF_SCENE_IDS: [7],
            CONF_TOPOLOGY: TOPOLOGY_PER_DEVICE,
        },
        options={OPT_LOAD_NAMES: {"1": "Kitchen", "2": "Hall"}},
        title="JS",
        unique_id="jetstream@/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)
    return entry


def _suffixes(hass, entry):
    reg = er.async_get(hass)
    return {
        row.unique_id.removeprefix(f"{entry.entry_id}_")
        for row in er.async_entries_for_config_entry(reg, entry.entry_id)
    }


async def _delete(hass, hass_ws_client, entry, device):
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": device.id,
        }
    )
    response = await client.receive_json()
    await hass.async_block_till_done(wait_background_tasks=True)
    return response


async def test_delete_unit_from_its_device_page(hass, hass_ws_client):
    entry = _jetstream_entry(hass)
    proto = FakeProtocol(supports_bulk=False, supports_scene_push=True)
    with patch(_JS, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = dr.async_get(hass)
        unit1 = registry.async_get_device_by_identifier(
            unit_identifier(entry.entry_id, 1), entry.entry_id
        )
        assert "load_001" in _suffixes(hass, entry)

        response = await _delete(hass, hass_ws_client, entry, unit1)
        assert response["success"], response

        # Gone from the stored lists, so a reload does not bring it back.
        assert entry.data[CONF_LOAD_IDS] == [2]
        assert entry.data[CONF_BUTTON_IDS] == [[2, 1]]
        assert "1" not in entry.options[OPT_LOAD_NAMES]
        assert registry.async_get(unit1.id) is None
        left = _suffixes(hass, entry)
        assert not {s for s in left if "_001" in s}, left
        assert {"load_002", "button_event_002_01", "scene_007"} <= left
        assert (
            registry.async_get_device_by_identifier(
                unit_identifier(entry.entry_id, 2), entry.entry_id
            )
            is not None
        )

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert not {s for s in _suffixes(hass, entry) if "_001" in s}
        assert (
            registry.async_get_device_by_identifier(
                unit_identifier(entry.entry_id, 1), entry.entry_id
            )
            is None
        )


async def test_the_bridge_device_cannot_be_deleted(hass, hass_ws_client):
    entry = _jetstream_entry(hass)
    proto = FakeProtocol(supports_bulk=False, supports_scene_push=True)
    with patch(_JS, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        bridge = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, entry.entry_id), entry.entry_id
        )
        response = await _delete(hass, hass_ws_client, entry, bridge)
        assert not response["success"]
        assert entry.data[CONF_LOAD_IDS] == [1, 2]
        assert dr.async_get(hass).async_get(bridge.id) is not None


async def test_the_elegance_device_cannot_be_deleted(hass, hass_ws_client):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SYSTEM_TYPE: SYSTEM_ELEGANCE,
            "port": "/dev/ttyUSB1",
            "baud": 19200,
            CONF_LOAD_IDS: [1],
            CONF_SWITCH_IDS: [5],
        },
        title="ELG",
        unique_id="elegance@/dev/ttyUSB1",
    )
    entry.add_to_hass(hass)
    with patch(_ELG, return_value=FakeProtocol(bulk_loads={1: True})):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        bridge = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, entry.entry_id), entry.entry_id
        )
        response = await _delete(hass, hass_ws_client, entry, bridge)
        assert not response["success"]
        assert entry.data[CONF_LOAD_IDS] == [1]
