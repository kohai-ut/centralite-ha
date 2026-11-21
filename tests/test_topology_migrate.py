"""Moving an existing JetStream entry to the per-device layout (opt-in)."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
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
    OPT_POLL_INTERVAL,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
)
from custom_components.centralite.jetstream_devices import (
    CONF_TOPOLOGY,
    TOPOLOGY_PER_DEVICE,
    is_per_device,
    unit_identifier,
)
from custom_components.centralite.topology_migrate import (
    async_prepare_units,
    build_preview,
)

from .conftest import FakeProtocol

_JS = "custom_components.centralite.protocol.jetstream.JetStreamProtocol"
_ELG = "custom_components.centralite.protocol.elegance.EleganceProtocol"


def _entry(hass, *, per_device=False):
    data = {
        CONF_SYSTEM_TYPE: SYSTEM_JETSTREAM,
        "port": "/dev/ttyUSB0",
        "baud": 19200,
        CONF_LOAD_IDS: [1, 2],
        CONF_BUTTON_IDS: [[1, 1], [44, 2]],
        CONF_SCENE_IDS: [7],
    }
    if per_device:
        data[CONF_TOPOLOGY] = TOPOLOGY_PER_DEVICE
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options={OPT_LOAD_NAMES: {"1": "Kitchen Cans", "2": "Hall"}},
        title="JS",
        unique_id="jetstream@/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry, path=_JS, proto=None):
    proto = proto or FakeProtocol(supports_bulk=False, supports_scene_push=True)
    with patch(path, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return proto


def _row(hass, entry, domain, suffix):
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{suffix}")
    return reg.async_get(eid) if eid else None


def _bridge(hass, entry):
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )


def _unit(hass, entry, idx):
    return dr.async_get(hass).async_get_device_by_identifier(
        unit_identifier(entry.entry_id, idx), entry.entry_id
    )


# --- Preview ---------------------------------------------------------------


async def test_preview_counts_what_will_change(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    er.async_get(hass).async_update_entity(
        _row(hass, entry, "light", "load_002").entity_id, name="Hallway"
    )

    preview = build_preview(hass, entry)

    assert preview.units == 3  # 1, 2 and the keypad-only 44
    assert preview.lights == 2
    assert sorted(preview.removed_switches) == sorted(
        [
            _row(hass, entry, "switch", "button_001_01").entity_id,
            _row(hass, entry, "switch", "button_044_02").entity_id,
        ]
    )
    assert preview.renamed_lights == [_row(hass, entry, "light", "load_002").entity_id]
    assert preview.bridge_disabled is False
    assert preview.references == []


async def test_preview_lists_automations_using_a_button_switch(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    switch_id = _row(hass, entry, "switch", "button_001_01").entity_id
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "id": "porch",
                "alias": "Porch on button",
                "trigger": {"platform": "state", "entity_id": switch_id, "to": "on"},
                "action": {"event": "noop"},
            }
        },
    )
    await hass.async_block_till_done()

    assert build_preview(hass, entry).references == ["automation.porch_on_button"]


# --- Options flow ----------------------------------------------------------


async def test_old_layout_jetstream_offers_a_menu(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"settings", "organize"}


async def test_settings_still_reachable_from_the_menu(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_POLL_INTERVAL: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][OPT_POLL_INTERVAL] == 120
    assert result["data"][OPT_LOAD_NAMES] == {"1": "Kitchen Cans", "2": "Hall"}


async def test_per_device_jetstream_is_not_offered_the_move_again(hass):
    entry = _entry(hass, per_device=True)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert "organize" not in result["menu_options"]
    assert "settings" in result["menu_options"]


async def test_organize_requires_confirmation(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "organize"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "organize"
    placeholders = result["description_placeholders"]
    assert placeholders["units"] == "3"
    assert placeholders["switches"] == "2"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"confirm": "confirm_required"}
    assert not is_per_device(entry.data)
    assert _unit(hass, entry, 1) is None


async def test_organize_moves_the_entry_and_keeps_areas(hass):
    entry = _entry(hass)
    proto = await _setup(hass, entry)
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    areas = ar.async_get(hass)
    kitchen, basement = areas.async_create("Kitchen"), areas.async_create("Basement")
    bridge = _bridge(hass, entry)
    dev_reg.async_update_device(bridge.id, area_id=basement.id, labels={"lighting"})
    light1 = _row(hass, entry, "light", "load_001")
    light2 = _row(hass, entry, "light", "load_002")
    ent_reg.async_update_entity(light1.entity_id, area_id=kitchen.id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "organize"}
    )
    with patch(_JS, return_value=proto):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirm": True}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY

    assert is_per_device(entry.data)
    unit1, unit2, unit44 = (_unit(hass, entry, i) for i in (1, 2, 44))
    # Same light rows, now on their units.
    after1 = _row(hass, entry, "light", "load_001")
    assert (after1.id, after1.entity_id) == (light1.id, light1.entity_id)
    assert after1.device_id == unit1.id
    assert _row(hass, entry, "light", "load_002").id == light2.id
    # A light with its own area hands it to the device; others follow the bridge.
    assert unit1.area_id == kitchen.id
    assert after1.area_id is None
    assert unit2.area_id == basement.id
    assert unit44.area_id == basement.id
    assert unit1.labels == {"lighting"}
    # Switches are replaced by the new entities.
    assert _row(hass, entry, "switch", "button_001_01") is None
    assert _row(hass, entry, "event", "button_event_001_01") is not None
    assert _row(hass, entry, "switch", "scene_007").device_id == bridge.id


async def test_prepare_does_not_overwrite_later_user_changes(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    dev_reg = dr.async_get(hass)
    den = ar.async_get(hass).async_create("Den")

    async_prepare_units(hass, entry)
    unit = _unit(hass, entry, 1)
    dev_reg.async_update_device(unit.id, area_id=den.id, labels={"mine"})
    async_prepare_units(hass, entry)

    unit = _unit(hass, entry, 1)
    assert unit.area_id == den.id
    assert unit.labels == {"mine"}


async def test_organize_refused_while_the_bridge_is_disabled(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    dr.async_get(hass).async_update_device(
        _bridge(hass, entry).id, disabled_by=dr.DeviceEntryDisabler.USER
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "organize"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "bridge_disabled"
    assert not is_per_device(entry.data)


# --- Repairs notice --------------------------------------------------------


def _issue(hass, entry):
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"jetstream_layout_{entry.entry_id}"
    )


async def test_old_layout_raises_one_notice(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False


async def test_per_device_layout_has_no_notice(hass):
    entry = _entry(hass, per_device=True)
    await _setup(hass, entry)
    assert _issue(hass, entry) is None


async def test_notice_clears_after_moving(hass):
    entry = _entry(hass)
    proto = await _setup(hass, entry)
    assert _issue(hass, entry) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TOPOLOGY: TOPOLOGY_PER_DEVICE}
    )
    await _setup(hass, entry, proto=proto)
    assert _issue(hass, entry) is None


async def test_elegance_gets_no_notice_and_no_menu(hass):
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
    await _setup(hass, entry, path=_ELG, proto=FakeProtocol(bulk_loads={1: True}))
    assert _issue(hass, entry) is None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM


async def test_empty_lists_are_not_shown_as_a_lone_hyphen(hass):
    """A lone "-" under a line makes Markdown render that line as a heading."""
    entry = _entry(hass)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "organize"}
    )
    placeholders = result["description_placeholders"]
    assert placeholders["references"].strip()
    assert all(value.strip() != "-" for value in placeholders.values())
