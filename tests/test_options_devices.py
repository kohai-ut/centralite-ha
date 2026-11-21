"""Options flow: add, change and remove JetStream devices after setup."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.components.light import ColorMode
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite import jetstream_devices
from custom_components.centralite.const import (
    CONF_BUTTON_IDS,
    CONF_DISABLED_LOADS,
    CONF_LOAD_IDS,
    CONF_SCENE_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    OPT_NONDIMMABLE_LOADS,
    SYSTEM_JETSTREAM,
)
from custom_components.centralite.jetstream_devices import (
    CONF_TOPOLOGY,
    TOPOLOGY_PER_DEVICE,
    unit_identifier,
)
from custom_components.centralite.protocol.jetstream import (
    JETSTREAM_MAX_BUTTONS_PER_SWITCH,
    JETSTREAM_MAX_LOADS,
    JETSTREAM_MAX_SWITCHES,
)

from .conftest import FakeProtocol

_JS = "custom_components.centralite.protocol.jetstream.JetStreamProtocol"


def test_limits_match_the_protocol():
    assert jetstream_devices.MAX_DEVICE_ID == JETSTREAM_MAX_LOADS == JETSTREAM_MAX_SWITCHES
    assert jetstream_devices.MAX_BUTTON == JETSTREAM_MAX_BUTTONS_PER_SWITCH


def _entry(hass, **data):
    base = {
        CONF_SYSTEM_TYPE: SYSTEM_JETSTREAM,
        "port": "/dev/ttyUSB0",
        "baud": 19200,
        CONF_LOAD_IDS: [1, 2],
        CONF_BUTTON_IDS: [[1, 1]],
        CONF_SCENE_IDS: [7],
        CONF_TOPOLOGY: TOPOLOGY_PER_DEVICE,
    }
    base.update(data)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=base,
        options={OPT_LOAD_NAMES: {"1": "Kitchen", "2": "Hall"}},
        title="JS",
        unique_id="jetstream@/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)
    return entry


class _Session:
    """Keeps the fake protocol patched in while the entry reloads."""

    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.proto = FakeProtocol(supports_bulk=False, supports_scene_push=True)
        self._patch = patch(_JS, return_value=self.proto)

    async def __aenter__(self):
        self._patch.start()
        assert await self.hass.config_entries.async_setup(self.entry.entry_id)
        await self.hass.async_block_till_done()
        return self

    async def __aexit__(self, *exc):
        await self.hass.async_block_till_done()
        self._patch.stop()

    async def menu(self, choice):
        result = await self.hass.config_entries.options.async_init(self.entry.entry_id)
        assert result["type"] is FlowResultType.MENU
        return await self.step(result, {"next_step_id": choice})

    async def step(self, result, user_input):
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"], user_input
        )
        await self.hass.async_block_till_done()
        return result


def _row(hass, entry, domain, suffix):
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{suffix}")
    return reg.async_get(eid) if eid else None


def _unit(hass, entry, idx):
    return dr.async_get(hass).async_get_device_by_identifier(
        unit_identifier(entry.entry_id, idx), entry.entry_id
    )


async def test_per_device_menu_offers_device_editing(hass):
    entry = _entry(hass)
    async with _Session(hass, entry):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.MENU
        assert set(result["menu_options"]) == {
            "settings",
            "add_device",
            "add_batch",
            "change_device",
            "remove_device",
        }


async def test_add_two_devices_then_save_reloads_once(hass):
    entry = _entry(hass)
    async with _Session(hass, entry) as s:
        kitchen = _row(hass, entry, "light", "load_001")

        result = await s.menu("add_device")
        assert result["type"] is FlowResultType.FORM
        result = await s.step(
            result,
            {"device_id": 81, "device_type": "dimmer", "buttons": ["1", "3"], "name": "Porch"},
        )
        assert result["type"] is FlowResultType.MENU
        assert set(result["menu_options"]) == {"add_device", "save_devices"}
        assert entry.data[CONF_LOAD_IDS] == [1, 2]  # nothing saved yet

        result = await s.step(result, {"next_step_id": "add_device"})
        result = await s.step(
            result, {"device_id": 90, "device_type": "keypad", "buttons": ["2"]}
        )
        result = await s.step(result, {"next_step_id": "save_devices"})
        assert result["type"] is FlowResultType.CREATE_ENTRY

        assert entry.data[CONF_LOAD_IDS] == [1, 2, 81]
        assert entry.data[CONF_BUTTON_IDS] == [[1, 1], [81, 1], [81, 3], [90, 2]]
        assert entry.options[OPT_LOAD_NAMES]["81"] == "Porch"
        # New entities exist, on their own devices.
        porch = _row(hass, entry, "light", "load_081")
        assert porch is not None
        assert porch.device_id == _unit(hass, entry, 81).id
        assert _unit(hass, entry, 81).name == "Porch"
        assert _row(hass, entry, "event", "button_event_081_03") is not None
        assert _row(hass, entry, "event", "button_event_090_02") is not None
        assert _row(hass, entry, "light", "load_090") is None  # keypad only
        # Existing entities are the same rows.
        after = _row(hass, entry, "light", "load_001")
        assert (after.id, after.entity_id) == (kitchen.id, kitchen.entity_id)


async def test_add_device_validation_errors(hass):
    entry = _entry(hass)
    async with _Session(hass, entry) as s:
        result = await s.menu("add_device")
        result = await s.step(
            result, {"device_id": 81, "device_type": "keypad", "buttons": []}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "keypad_needs_button"}
        assert entry.data[CONF_LOAD_IDS] == [1, 2]


async def test_adding_buttons_to_an_existing_light_keeps_it(hass):
    entry = _entry(hass)
    async with _Session(hass, entry) as s:
        hall = _row(hass, entry, "light", "load_002")
        result = await s.menu("add_device")
        result = await s.step(
            result, {"device_id": 2, "device_type": "switch", "buttons": ["1"]}
        )
        result = await s.step(result, {"next_step_id": "save_devices"})
        assert result["type"] is FlowResultType.CREATE_ENTRY

        assert _row(hass, entry, "light", "load_002").id == hall.id
        assert _row(hass, entry, "event", "button_event_002_01") is not None
        # Still a dimmer: adding never retypes.
        assert 2 not in entry.options.get(OPT_NONDIMMABLE_LOADS, [])


async def test_adding_a_load_imported_disabled_enables_it(hass):
    entry = _entry(hass, **{CONF_DISABLED_LOADS: [2]})
    async with _Session(hass, entry) as s:
        assert (
            _row(hass, entry, "light", "load_002").disabled_by
            is er.RegistryEntryDisabler.INTEGRATION
        )
        result = await s.menu("add_device")
        result = await s.step(result, {"device_id": 2, "device_type": "dimmer", "buttons": []})
        result = await s.step(result, {"next_step_id": "save_devices"})
        assert result["type"] is FlowResultType.CREATE_ENTRY

        assert _row(hass, entry, "light", "load_002").disabled_by is None
        assert entry.data[CONF_DISABLED_LOADS] == []


async def test_adding_does_not_override_a_user_disable(hass):
    entry = _entry(hass)
    async with _Session(hass, entry) as s:
        er.async_get(hass).async_update_entity(
            _row(hass, entry, "light", "load_002").entity_id,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        result = await s.menu("add_device")
        result = await s.step(result, {"device_id": 2, "device_type": "dimmer", "buttons": ["2"]})
        result = await s.step(result, {"next_step_id": "save_devices"})

        assert (
            _row(hass, entry, "light", "load_002").disabled_by
            is er.RegistryEntryDisabler.USER
        )


async def test_batch_entry(hass):
    entry = _entry(hass)
    async with _Session(hass, entry) as s:
        result = await s.menu("add_batch")
        result = await s.step(result, {"batch": "81, dimmer, 1\n82, on/off\nxx, dimmer"})
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"batch": "bad_line"}
        assert result["description_placeholders"]["line"] == "3"

        result = await s.step(result, {"batch": "81, dimmer, 1\n82, on/off"})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.data[CONF_LOAD_IDS] == [1, 2, 81, 82]
        assert entry.options[OPT_NONDIMMABLE_LOADS] == [82]
        state = hass.states.get(_row(hass, entry, "light", "load_082").entity_id)
        assert state.attributes["supported_color_modes"] == [ColorMode.ONOFF]


async def test_change_device_between_dimmer_and_on_off(hass):
    entry = _entry(hass)
    async with _Session(hass, entry) as s:
        hall = _row(hass, entry, "light", "load_002")
        result = await s.menu("change_device")
        assert result["type"] is FlowResultType.FORM
        result = await s.step(result, {"device": "2"})
        # The second screen starts on what the light is now, not on a fixed
        # choice, so submitting it unread changes nothing.
        assert result["step_id"] == "change_type"
        assert result["data_schema"]({})["device_type"] == "dimmer"
        assert "Hall" in result["description_placeholders"]["device"]
        result = await s.step(result, {"device_type": "switch"})
        assert result["type"] is FlowResultType.CREATE_ENTRY

        assert entry.options[OPT_NONDIMMABLE_LOADS] == [2]
        after = _row(hass, entry, "light", "load_002")
        assert (after.id, after.entity_id) == (hall.id, hall.entity_id)
        state = hass.states.get(after.entity_id)
        assert state.attributes["supported_color_modes"] == [ColorMode.ONOFF]


async def test_change_screen_starts_on_on_off_for_an_on_off_light(hass):
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, OPT_NONDIMMABLE_LOADS: [2]}
    )
    async with _Session(hass, entry) as s:
        result = await s.menu("change_device")
        result = await s.step(result, {"device": "2"})
        assert result["data_schema"]({})["device_type"] == "switch"
        result = await s.step(result, {"device_type": "switch"})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[OPT_NONDIMMABLE_LOADS] == [2]


async def test_remove_device_needs_confirmation_and_deletes_only_that_unit(hass):
    entry = _entry(hass)
    async with _Session(hass, entry) as s:
        hall = _row(hass, entry, "light", "load_002")
        unit1 = _unit(hass, entry, 1)

        result = await s.menu("remove_device")
        result = await s.step(result, {"device": "1"})
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "remove_confirm"
        listed = result["description_placeholders"]["entities"]
        assert _row(hass, entry, "light", "load_001").entity_id in listed
        assert _row(hass, entry, "event", "button_event_001_01").entity_id in listed

        result = await s.step(result, {"confirm": False})
        assert result["errors"] == {"confirm": "confirm_required"}
        assert entry.data[CONF_LOAD_IDS] == [1, 2]

        result = await s.step(result, {"confirm": True})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.data[CONF_LOAD_IDS] == [2]
        assert entry.data[CONF_BUTTON_IDS] == []
        assert "1" not in entry.options[OPT_LOAD_NAMES]
        for domain, suffix in (
            ("light", "load_001"),
            ("event", "button_event_001_01"),
            ("button", "button_press_001_01"),
            ("button", "button_hold_001_01"),
        ):
            assert _row(hass, entry, domain, suffix) is None, suffix
        assert dr.async_get(hass).async_get(unit1.id) is None
        assert _row(hass, entry, "light", "load_002").id == hall.id
        assert _row(hass, entry, "switch", "scene_007") is not None
