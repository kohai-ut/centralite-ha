"""Config + options flow tests.

Covers the user->import two-step flow, the regressions fixed in this branch
(out-of-range ID rejection, unrecognized-paste honesty, eLite removed), the
duplicate-bridge abort, and the options flow no longer carrying append_cr.
"""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite.config_flow import SYSTEM_OPTIONS
from custom_components.centralite.const import (
    CONF_BAUD,
    CONF_LOAD_IDS,
    CONF_PORT,
    CONF_SCENE_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    OPT_POLL_INTERVAL,
    SYSTEM_ELEGANCE,
    SYSTEM_ELITE,
)

_USER_INPUT = {CONF_SYSTEM_TYPE: SYSTEM_ELEGANCE, CONF_PORT: "/dev/ttyUSB0", CONF_BAUD: 19200}


async def _advance_to_import(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "import"
    return result


def test_elite_not_offered():
    """Regression: eLite has no setup path, so it must not be selectable."""
    values = {opt["value"] for opt in SYSTEM_OPTIONS}
    assert SYSTEM_ELITE not in values
    assert values == {"elegance", "jetstream"}


async def test_full_flow_with_elg_paste(hass):
    result = await _advance_to_import(hass)
    elg = "[LOAD 1]\nNAME=Kitchen\n[LOAD 2]\nNAME=Hall\n[SCENE 3- Movie Night]\n"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"elg_text": elg, "load_ids_csv": "", "scene_ids_csv": "", "switch_ids_csv": ""},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LOAD_IDS] == [1, 2]
    assert result["data"][CONF_SCENE_IDS] == [3]
    assert result["options"][OPT_LOAD_NAMES] == {"1": "Kitchen", "2": "Hall"}


async def test_csv_only_flow(hass):
    result = await _advance_to_import(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"elg_text": "", "load_ids_csv": "1,2,3", "scene_ids_csv": "5", "switch_ids_csv": "10"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LOAD_IDS] == [1, 2, 3]


async def test_out_of_range_id_rejected(hass):
    """Regression: an ID above the system max must error, not create a dead entity."""
    result = await _advance_to_import(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"elg_text": "", "load_ids_csv": "999", "scene_ids_csv": "", "switch_ids_csv": ""},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "ids_out_of_range"}


async def test_unrecognized_paste_errors(hass):
    """Regression: non-.elg paste must surface an error, not import zero devices."""
    result = await _advance_to_import(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "elg_text": "this is not an elg file at all",
            "load_ids_csv": "",
            "scene_ids_csv": "",
            "switch_ids_csv": "",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "import_failed"}


async def test_bad_csv_errors(hass):
    result = await _advance_to_import(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"elg_text": "", "load_ids_csv": "1,abc", "scene_ids_csv": "", "switch_ids_csv": ""},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "import_failed"}


async def test_duplicate_bridge_aborts(hass):
    existing = MockConfigEntry(
        domain=DOMAIN, data=_USER_INPUT, unique_id="elegance@/dev/ttyUSB0"
    )
    existing.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_has_no_append_cr(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=_USER_INPUT, options={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema_keys = {str(k.schema) for k in result["data_schema"].schema}
    assert OPT_POLL_INTERVAL in schema_keys
    assert "append_cr" not in schema_keys
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_POLL_INTERVAL: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][OPT_POLL_INTERVAL] == 120
    assert "append_cr" not in result["data"]
