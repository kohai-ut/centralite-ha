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
    CONF_BUTTON_IDS,
    CONF_DISABLED_LOADS,
    CONF_LOAD_IDS,
    CONF_PORT,
    CONF_SCENE_IDS,
    CONF_SWITCH_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    OPT_NONDIMMABLE_LOADS,
    OPT_POLL_INTERVAL,
    OPT_SWITCH_NAMES,
    OPT_SYNC_CLOCK_ON_CONNECT,
    SYSTEM_ELEGANCE,
    SYSTEM_ELITE,
    SYSTEM_JETSTREAM,
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


async def test_elg_import_records_nondimmable_loads(hass):
    """DIMMER=N loads from the .elg are stored so they become on/off lights."""
    result = await _advance_to_import(hass)
    elg = (
        "[LOAD 1]\nNAME=Recessed\nDIMMER=Y\n"
        "[LOAD 2]\nNAME=Closet\nDIMMER=N\n"
        "[LOAD 3]\nNAME=Lamp\nDIMMER=N\n"
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"elg_text": elg, "load_ids_csv": "", "scene_ids_csv": "", "switch_ids_csv": ""},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LOAD_IDS] == [1, 2, 3]
    assert result["options"][OPT_NONDIMMABLE_LOADS] == [2, 3]


async def test_elg_import_skips_phantom_loads(hass):
    """Only named or referenced loads are created; unused slots are skipped.

    Load 1 named, load 18 referenced-by-scene (unnamed), load 50 = phantom
    (unnamed, unreferenced) -> skipped. Load 18 -> created but disabled.
    """
    result = await _advance_to_import(hass)
    elg = (
        "[LOAD 1]\nNAME=Hall\nDIMMER=Y\n"
        "[LOAD 18]\nNAME=\nDIMMER=Y\n"
        "[LOAD 50]\nNAME=\nDIMMER=Y\n"
        "[SCENE 04- Path]\nNAME=Path\nLOAD_001- Hall\nLOAD_018- Landing\n"
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"elg_text": elg, "load_ids_csv": "", "scene_ids_csv": "", "switch_ids_csv": ""},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LOAD_IDS] == [1, 18]  # 50 (phantom) skipped
    assert result["data"][CONF_DISABLED_LOADS] == [18]  # referenced but unnamed


async def test_elg_import_creates_named_switches(hass):
    """Named keypad buttons in the .elg become switch entities with names."""
    result = await _advance_to_import(hass)
    elg = (
        "[LOAD 1]\nNAME=Hall\n"
        "[E4]\nNAME=North Garage Lights\nLOAD/SCENE=L\n"  # -> switch idx 53
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"elg_text": elg, "load_ids_csv": "", "scene_ids_csv": "", "switch_ids_csv": ""},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SWITCH_IDS] == [53]
    assert result["options"][OPT_SWITCH_NAMES] == {"53": "North Garage Lights"}


async def test_manual_load_ids_always_enabled(hass):
    """A hand-typed load ID is never disabled even if also unnamed in the .elg."""
    result = await _advance_to_import(hass)
    elg = "[LOAD 18]\nNAME=\n[SCENE 04- P]\nNAME=P\nLOAD_018- x\n"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"elg_text": elg, "load_ids_csv": "18", "scene_ids_csv": "", "switch_ids_csv": ""},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LOAD_IDS] == [18]
    assert CONF_DISABLED_LOADS not in result["data"]  # explicit entry => enabled


async def test_jts_import_jetstream(hass):
    """Pasting a JetStream .jts populates loads (with names + dimmable) and scenes."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SYSTEM_TYPE: "jetstream", CONF_PORT: "/dev/ttyUSB0", CONF_BAUD: 19200},
    )
    assert result["step_id"] == "import"
    jts = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<GulfStreamCL><DeviceList>"
        "<Device><DeviceID>2</DeviceID><Name>Game Cans</Name>"
        "<Dimmer>true</Dimmer><SendThirdParty>true</SendThirdParty><Active>true</Active></Device>"
        "<Device><DeviceID>3</DeviceID><Name>Hall Relay</Name>"
        "<Dimmer>false</Dimmer><SendThirdParty>true</SendThirdParty><Active>true</Active>"
        "<buttonList>"
        "<Button><ID>0</ID><tap><BtnAction>1</BtnAction></tap></Button>"
        "<Button><ID>1</ID><tap><BtnAction>5</BtnAction></tap></Button>"
        "</buttonList></Device>"
        "</DeviceList><SceneList>"
        "<Scene><ID>1</ID><Name>All On</Name></Scene>"
        "</SceneList></GulfStreamCL>"
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"elg_text": jts, "load_ids_csv": "", "scene_ids_csv": "", "switch_ids_csv": ""},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LOAD_IDS] == [2, 3]
    assert result["data"][CONF_SCENE_IDS] == [1]
    assert result["options"][OPT_LOAD_NAMES] == {"2": "Game Cans", "3": "Hall Relay"}
    assert result["options"][OPT_NONDIMMABLE_LOADS] == [3]  # Dimmer=false
    assert CONF_DISABLED_LOADS not in result["data"]  # .jts devices are all real
    # device 3's configured buttons (ID 0,1 -> protocol 1,2) become button switches
    assert result["data"][CONF_BUTTON_IDS] == [[3, 1], [3, 2]]
    assert result["options"][OPT_SWITCH_NAMES]["003.01"] == "Hall Relay Button 1"


class _FakeScanProto:
    """Stand-in for JetStreamProtocol used by the config-flow scan tests."""

    def __init__(
        self, port, baudrate=19200, *, names=None, connect_error=None, scan_error=None
    ):
        self._names = names or {}
        self._connect_error = connect_error
        self._scan_error = scan_error

    async def connect(self):
        if self._connect_error:
            raise self._connect_error

    async def disconnect(self):
        pass

    async def scan_device_names(self, **kwargs):
        if self._scan_error:
            raise self._scan_error
        return self._names


async def _jetstream_to_import(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SYSTEM_TYPE: "jetstream", CONF_PORT: "/dev/ttyUSB0", CONF_BAUD: 19200},
    )
    assert result["step_id"] == "import"
    return result


async def test_jetstream_scan_import(hass):
    """Checking 'scan' discovers device names from the bridge and creates loads."""
    import functools

    from unittest.mock import patch

    result = await _jetstream_to_import(hass)
    fake = functools.partial(_FakeScanProto, names={2: "Game Cans", 7: "Den"})
    with patch("custom_components.centralite.config_flow.JetStreamProtocol", fake):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"elg_text": "", "load_ids_csv": "", "scene_ids_csv": "",
             "switch_ids_csv": "", "scan_jetstream": True},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LOAD_IDS] == [2, 7]
    assert result["options"][OPT_LOAD_NAMES] == {"2": "Game Cans", "7": "Den"}


async def test_jetstream_scan_connection_failure(hass):
    from unittest.mock import patch

    import functools

    result = await _jetstream_to_import(hass)
    fake = functools.partial(_FakeScanProto, connect_error=OSError("no device"))
    with patch("custom_components.centralite.config_flow.JetStreamProtocol", fake):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"elg_text": "", "load_ids_csv": "", "scene_ids_csv": "",
             "switch_ids_csv": "", "scan_jetstream": True},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "scan_failed"}


async def test_jetstream_scan_disconnect_midway(hass):
    """A mid-scan disconnect (ProtocolError) surfaces as scan_failed, not a crash."""
    import functools
    from unittest.mock import patch

    from custom_components.centralite.protocol.common import ProtocolError

    result = await _jetstream_to_import(hass)
    fake = functools.partial(_FakeScanProto, scan_error=ProtocolError("connection lost"))
    with patch("custom_components.centralite.config_flow.JetStreamProtocol", fake):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"elg_text": "", "load_ids_csv": "", "scene_ids_csv": "",
             "switch_ids_csv": "", "scan_jetstream": True},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "scan_failed"}


async def test_elegance_rejects_xml_paste(hass):
    """Routing is by system type: an Elegance entry sends XML to the .elg parser,
    which finds nothing and reports import_failed (rather than misrouting to .jts)."""
    result = await _advance_to_import(hass)  # Elegance
    xml = "<?xml version='1.0'?><GulfStreamCL><DeviceList></DeviceList></GulfStreamCL>"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"elg_text": xml, "load_ids_csv": "", "scene_ids_csv": "", "switch_ids_csv": ""},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "import_failed"}


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


async def test_options_flow_elegance_persists_clock_sync(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=_USER_INPUT, options={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_keys = {str(k.schema) for k in result["data_schema"].schema}
    assert OPT_SYNC_CLOCK_ON_CONNECT in schema_keys  # offered for Elegance
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {OPT_POLL_INTERVAL: 300, OPT_SYNC_CLOCK_ON_CONNECT: True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][OPT_SYNC_CLOCK_ON_CONNECT] is True


async def test_options_flow_jetstream_hides_clock_sync(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**_USER_INPUT, CONF_SYSTEM_TYPE: SYSTEM_JETSTREAM},
        options={},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_keys = {str(k.schema) for k in result["data_schema"].schema}
    assert OPT_SYNC_CLOCK_ON_CONNECT not in schema_keys  # no clock on JetStream
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_POLL_INTERVAL: 300}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert OPT_SYNC_CLOCK_ON_CONNECT not in result["data"]
