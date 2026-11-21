"""Per-physical-device layout for JetStream (real platform setup, fake protocol).

Covers: the layout flag decides device association; a missing flag keeps the
original single-bridge layout; flipping the flag re-links the SAME entity
registry rows; scenes and Elegance are untouched.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite.const import (
    CONF_BUTTON_IDS,
    CONF_DISABLED_LOADS,
    CONF_LOAD_IDS,
    CONF_SCENE_IDS,
    CONF_SWITCH_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    OPT_SCENE_NAMES,
    OPT_SWITCH_NAMES,
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


def _js_entry(hass, *, per_device: bool, **data):
    base = {
        CONF_SYSTEM_TYPE: SYSTEM_JETSTREAM,
        "port": "/dev/ttyUSB0",
        "baud": 19200,
        CONF_LOAD_IDS: [1, 2],
        CONF_BUTTON_IDS: [[1, 1], [44, 2]],
        CONF_SCENE_IDS: [7],
    }
    base.update(data)
    if per_device:
        base[CONF_TOPOLOGY] = TOPOLOGY_PER_DEVICE
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=base,
        options={
            OPT_LOAD_NAMES: {"1": "Kitchen Cans", "2": "Hall"},
            OPT_SWITCH_NAMES: {"001.01": "Kitchen Cans Button 1"},
            OPT_SCENE_NAMES: {"7": "Night Scene"},
        },
        title="JS",
        unique_id="jetstream@/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry, path=_JS):
    proto = FakeProtocol(supports_bulk=False, supports_scene_push=True)
    with patch(path, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return proto


def _row(hass, entry, domain, suffix):
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{suffix}")
    assert eid, suffix
    return reg.async_get(eid)


def _bridge(hass, entry):
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )


def _unit(hass, entry, idx):
    return dr.async_get(hass).async_get_device_by_identifier(
        unit_identifier(entry.entry_id, idx), entry.entry_id
    )


async def test_missing_flag_keeps_everything_on_the_bridge(hass):
    entry = _js_entry(hass, per_device=False)
    await _setup(hass, entry)

    bridge = _bridge(hass, entry)
    for domain, suffix in (
        ("light", "load_001"),
        ("switch", "button_001_01"),
        ("switch", "scene_007"),
    ):
        assert _row(hass, entry, domain, suffix).device_id == bridge.id
    assert _unit(hass, entry, 1) is None
    # Names are unchanged in the original layout.
    assert _row(hass, entry, "light", "load_001").original_name == "Kitchen Cans"


async def test_per_device_puts_a_light_and_its_button_on_one_unit(hass):
    entry = _js_entry(hass, per_device=True)
    await _setup(hass, entry)

    bridge = _bridge(hass, entry)
    unit = _unit(hass, entry, 1)
    assert unit is not None
    assert unit.name == "Kitchen Cans"
    assert unit.via_device_id == bridge.id
    assert unit.manufacturer == bridge.manufacturer
    assert _row(hass, entry, "light", "load_001").device_id == unit.id
    assert _row(hass, entry, "event", "button_event_001_01").device_id == unit.id


async def test_per_device_light_is_the_main_entity_of_its_device(hass):
    entry = _js_entry(hass, per_device=True)
    await _setup(hass, entry)

    light = _row(hass, entry, "light", "load_001")
    assert light.original_name is None
    # No name of its own: it is shown as the device, not "Kitchen Cans Kitchen Cans".
    assert hass.states.get(light.entity_id).attributes["friendly_name"] == "Kitchen Cans"


async def test_per_device_keypad_only_unit_has_no_light(hass):
    entry = _js_entry(hass, per_device=True)
    await _setup(hass, entry)

    unit = _unit(hass, entry, 44)
    assert unit is not None
    assert unit.name == "Device 044"  # nothing imported for it
    rows = er.async_entries_for_device(er.async_get(hass), unit.id)
    assert sorted({r.domain for r in rows}) == ["button", "event"]


async def test_per_device_scenes_stay_on_the_bridge(hass):
    entry = _js_entry(hass, per_device=True)
    await _setup(hass, entry)

    assert _row(hass, entry, "switch", "scene_007").device_id == _bridge(hass, entry).id


async def test_bridge_is_registered_even_with_no_bridge_entities(hass):
    entry = _js_entry(hass, per_device=True, **{CONF_SCENE_IDS: []})
    await _setup(hass, entry)

    bridge = _bridge(hass, entry)
    assert bridge is not None
    assert er.async_entries_for_device(er.async_get(hass), bridge.id) == []
    assert _unit(hass, entry, 1).via_device_id == bridge.id


async def test_unit_exists_for_a_load_created_disabled(hass):
    entry = _js_entry(hass, per_device=True, **{CONF_DISABLED_LOADS: [2]})
    await _setup(hass, entry)

    light = _row(hass, entry, "light", "load_002")
    assert light.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert light.device_id == _unit(hass, entry, 2).id


async def test_flipping_the_flag_relinks_the_same_rows(hass):
    entry = _js_entry(hass, per_device=False)
    await _setup(hass, entry)
    ent_reg = er.async_get(hass)
    before = {
        key: _row(hass, entry, domain, suffix)
        for key, (domain, suffix) in {
            "light1": ("light", "load_001"),
            "light2": ("light", "load_002"),
            "button": ("switch", "button_001_01"),
            "scene": ("switch", "scene_007"),
        }.items()
    }
    count_before = len(er.async_entries_for_config_entry(ent_reg, entry.entry_id))
    kitchen = ar.async_get(hass).async_create("Kitchen")
    ent_reg.async_update_entity(
        before["light1"].entity_id, name="Island", icon="mdi:island", area_id=kitchen.id
    )
    ent_reg.async_update_entity(
        before["light2"].entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TOPOLOGY: TOPOLOGY_PER_DEVICE}
    )
    await _setup(hass, entry)

    bridge = _bridge(hass, entry)
    after = {
        "light1": _row(hass, entry, "light", "load_001"),
        "light2": _row(hass, entry, "light", "load_002"),
        "scene": _row(hass, entry, "switch", "scene_007"),
    }
    for key, row in after.items():
        assert row.id == before[key].id, key
        assert row.entity_id == before[key].entity_id, key
    # Lights and scenes are the same rows; only the keypad buttons change kind
    # (covered in test_button_entities.py).
    assert count_before == 5
    assert after["light1"].device_id == _unit(hass, entry, 1).id
    assert after["light2"].device_id == _unit(hass, entry, 2).id
    assert after["scene"].device_id == bridge.id
    # The user's own settings survive the move.
    assert after["light1"].name == "Island"
    assert after["light1"].icon == "mdi:island"
    assert after["light1"].area_id == kitchen.id
    assert after["light2"].disabled_by is er.RegistryEntryDisabler.USER


async def test_reload_does_not_overwrite_a_renamed_unit_device(hass):
    entry = _js_entry(hass, per_device=True)
    await _setup(hass, entry)
    dev_reg = dr.async_get(hass)
    dev_reg.async_update_device(_unit(hass, entry, 1).id, name_by_user="Island cans")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await _setup(hass, entry)

    unit = _unit(hass, entry, 1)
    assert unit.name_by_user == "Island cans"
    assert len(dr.async_entries_for_config_entry(dev_reg, entry.entry_id)) == 4


async def test_elegance_ignores_the_flag(hass):
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
    proto = FakeProtocol(bulk_loads={1: True})
    with patch(_ELG, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    bridge = _bridge(hass, entry)
    assert _row(hass, entry, "light", "load_001").device_id == bridge.id
    assert _row(hass, entry, "switch", "switch_005").device_id == bridge.id
    assert len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1


async def test_button_event_names_the_bridge_without_deprecated_lookup(hass, caplog):
    entry = _js_entry(hass, per_device=True)
    proto = await _setup(hass, entry)
    events = []
    hass.bus.async_listen("centralite_event", events.append)

    from custom_components.centralite.protocol import SwitchEvent

    proto.switch_cb(SwitchEvent(idx=1, button=1, action="tap"))
    await hass.async_block_till_done()

    assert [e.data["device_id"] for e in events] == [_bridge(hass, entry).id]
    assert "async_get_device`" not in caplog.text
