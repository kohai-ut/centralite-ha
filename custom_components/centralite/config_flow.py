"""Config flow + options flow for Centralite."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_BAUD,
    CONF_BUTTON_IDS,
    CONF_DISABLED_LOADS,
    CONF_LOAD_IDS,
    CONF_PORT,
    CONF_SCENE_IDS,
    CONF_SWITCH_IDS,
    CONF_SYSTEM_TYPE,
    DEFAULT_BAUD,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    OPT_LOAD_NAMES,
    OPT_NONDIMMABLE_LOADS,
    OPT_POLL_INTERVAL,
    OPT_SCENE_NAMES,
    OPT_SWITCH_NAMES,
    OPT_SYNC_CLOCK_ON_CONNECT,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
    SYSTEM_LABELS,
)
from .jetstream_devices import (
    CONF_TOPOLOGY,
    MAX_DEVICE_ID,
    TOPOLOGY_PER_DEVICE,
    TYPE_DIMMER,
    TYPE_KEYPAD,
    TYPE_SWITCH,
    DeviceAddition,
    DeviceEditError,
    change_load_type,
    is_per_device,
    merge_additions,
    parse_batch,
    remove_unit,
    unit_addresses,
    unit_name,
)
from .parsers.elg import parse_csv_ids, parse_elg
from .parsers.jts import parse_jts
from .protocol.common import MAX_LOADS as ELEGANCE_MAX_LOADS
from .protocol.common import MAX_SWITCHES as ELEGANCE_MAX_SWITCHES
from .protocol.elegance import MAX_SCENES as ELEGANCE_MAX_SCENES
from .protocol.jetstream import (
    JETSTREAM_MAX_LOADS,
    JETSTREAM_MAX_SCENES,
    JETSTREAM_MAX_SWITCHES,
    JetStreamProtocol,
)

# Valid ID ranges per system, used to reject out-of-range IDs at config time
# instead of letting them become entities that silently fail on first command.
_SYSTEM_LIMITS: dict[str, dict[str, int]] = {
    SYSTEM_ELEGANCE: {
        "load": ELEGANCE_MAX_LOADS,
        "scene": ELEGANCE_MAX_SCENES,
        "switch": ELEGANCE_MAX_SWITCHES,
    },
    SYSTEM_JETSTREAM: {
        "load": JETSTREAM_MAX_LOADS,
        "scene": JETSTREAM_MAX_SCENES,
        "switch": JETSTREAM_MAX_SWITCHES,
    },
}


class _IdRangeError(ValueError):
    """An imported/entered ID falls outside the valid range for the system."""


class _ScanError(Exception):
    """The optional JetStream ^N discovery scan could not reach the bridge."""


def _check_range(kind: str, ids: set[int], system_type: str) -> None:
    """Raise _IdRangeError if any id is <1 or above the system's max for kind."""
    limit = _SYSTEM_LIMITS[system_type][kind]
    bad = sorted(i for i in ids if i < 1 or i > limit)
    if bad:
        raise _IdRangeError(
            f"{kind} IDs out of range 1-{limit} for {system_type}: {bad}"
        )

_LOGGER = logging.getLogger(__name__)

# Only systems with a working setup path in __init__.async_setup_entry belong
# here. eLite (SYSTEM_ELITE) is reserved in const.py but has no protocol
# implementation yet; offering it would let a user create an entry that can
# never load. Add it back the moment EliteProtocol exists.
SYSTEM_OPTIONS = [
    {"value": SYSTEM_ELEGANCE, "label": "Centralite Elegance"},
    {"value": SYSTEM_JETSTREAM, "label": "Centralite JetStream"},
]

_PORT_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
_CSV_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
_MULTILINE_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
)
_SYSTEM_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=SYSTEM_OPTIONS, mode=SelectSelectorMode.DROPDOWN)
)
_BAUD_SELECTOR = NumberSelector(
    NumberSelectorConfig(min=1200, max=115200, step=1, mode=NumberSelectorMode.BOX)
)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_SYSTEM_TYPE, default=d.get(CONF_SYSTEM_TYPE, SYSTEM_ELEGANCE)
            ): _SYSTEM_SELECTOR,
            vol.Required(CONF_PORT, default=d.get(CONF_PORT, "")): _PORT_SELECTOR,
            vol.Required(CONF_BAUD, default=d.get(CONF_BAUD, DEFAULT_BAUD)): _BAUD_SELECTOR,
        }
    )


def _import_schema(system_type: str) -> vol.Schema:
    schema: dict[Any, Any] = {
        vol.Optional("elg_text", default=""): _MULTILINE_SELECTOR,
        vol.Optional("load_ids_csv", default=""): _CSV_SELECTOR,
        vol.Optional("scene_ids_csv", default=""): _CSV_SELECTOR,
        vol.Optional("switch_ids_csv", default=""): _CSV_SELECTOR,
    }
    if system_type == SYSTEM_JETSTREAM:
        # JetStream-only: discover device names by scanning the bridge (^N).
        # No effect for Elegance, which has no per-device name query.
        schema[vol.Optional("scan_jetstream", default=False)] = BooleanSelector()
    return vol.Schema(schema)


class CentraliteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Centralite config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_SYSTEM_TYPE] = user_input[CONF_SYSTEM_TYPE]
            self._data[CONF_PORT] = user_input[CONF_PORT].strip()
            self._data[CONF_BAUD] = int(user_input[CONF_BAUD])
            await self.async_set_unique_id(
                f"{self._data[CONF_SYSTEM_TYPE]}@{self._data[CONF_PORT]}"
            )
            self._abort_if_unique_id_configured()
            return await self.async_step_import()
        return self.async_show_form(step_id="user", data_schema=_user_schema())

    async def async_step_import(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                load_names: dict[str, str] = {}
                scene_names: dict[str, str] = {}
                switch_names: dict[str, str] = {}
                load_ids: set[int] = set()
                scene_ids: set[int] = set()
                switch_ids: set[int] = set()
                button_pairs: set[tuple[int, int]] = set()  # JetStream (device, button)
                nondimmable: set[int] = set()
                disabled: set[int] = set()
                jts_duplicates: dict[int, tuple[str, ...]] = {}

                import_text = user_input.get("elg_text", "").strip()
                is_jetstream = self._data[CONF_SYSTEM_TYPE] == SYSTEM_JETSTREAM
                if import_text and is_jetstream:
                    # JetStream uses the .jts (XML) parser. Route by the selected
                    # system type, not by sniffing the content, so a BOM-prefixed
                    # file or wrong-format paste fails cleanly instead of being
                    # parsed by the wrong parser. Every .jts device is real (no
                    # phantom slots), so all are created and enabled.
                    jts = parse_jts(import_text)
                    if not jts.loads and not jts.scenes:
                        raise ValueError("unrecognized import text")
                    jts_duplicates = jts.duplicates
                    for idx, name in jts.loads.items():
                        load_ids.add(idx)
                        if name:
                            load_names[str(idx)] = name
                        if not jts.dimmable.get(idx, True):
                            nondimmable.add(idx)
                    for idx, name in jts.scenes.items():
                        scene_ids.add(idx)
                        if name:
                            scene_names[str(idx)] = name
                    # Configured keypad buttons -> button switch entities.
                    for (dev, btn), label in jts.buttons.items():
                        button_pairs.add((dev, btn))
                        if label:
                            switch_names[f"{dev:03d}.{btn:02d}"] = label
                elif import_text:
                    # Elegance (and reserved eLite) use the .elg INI parser.
                    cfg = parse_elg(import_text)
                    if not cfg.loads and not cfg.scenes:
                        # Non-empty paste that yielded nothing: not a recognized
                        # Elegance .elg file or the wrong file. Tell the user
                        # rather than silently importing zero devices.
                        raise ValueError("unrecognized import text")
                    # A .elg lists all 192 load slots, most unnamed/unused. Create
                    # only loads that are named OR referenced by a scene/keypad
                    # button; skip the phantom defaults. Named loads are enabled;
                    # used-but-unnamed loads are created disabled-by-default so
                    # they can be turned on in HA's UI without a re-import.
                    named = {idx for idx, name in cfg.loads.items() if name}
                    used = named | (cfg.referenced_loads & set(cfg.loads))
                    for idx in used:
                        load_ids.add(idx)
                        name = cfg.loads.get(idx, "")
                        if name:
                            load_names[str(idx)] = name
                        else:
                            disabled.add(idx)
                        if not cfg.dimmable.get(idx, True):
                            nondimmable.add(idx)
                    for idx, name in cfg.scenes.items():
                        scene_ids.add(idx)
                        if name:
                            scene_names[str(idx)] = name
                    # Named keypad buttons -> switch entities. The
                    # [letter][number] -> global switch index mapping is
                    # (letter-'A')*24 + number (see parsers.elg._named_switches),
                    # confirmed against a real install.
                    for idx, name in cfg.switches.items():
                        switch_ids.add(idx)
                        switch_names[str(idx)] = name

                # IDs typed by hand are explicit intent: always created enabled.
                csv_loads = set(parse_csv_ids(user_input.get("load_ids_csv", "")))
                load_ids.update(csv_loads)
                disabled -= csv_loads
                scene_ids.update(parse_csv_ids(user_input.get("scene_ids_csv", "")))
                switch_ids.update(parse_csv_ids(user_input.get("switch_ids_csv", "")))

                # Optional one-time discovery scan of JetStream device names.
                if (
                    user_input.get("scan_jetstream")
                    and self._data[CONF_SYSTEM_TYPE] == SYSTEM_JETSTREAM
                ):
                    for idx, name in (await self._scan_jetstream_names()).items():
                        load_ids.add(idx)
                        load_names[str(idx)] = name

                system_type = self._data[CONF_SYSTEM_TYPE]
                _check_range("load", load_ids, system_type)
                _check_range("scene", scene_ids, system_type)
                _check_range("switch", switch_ids, system_type)

                self._data[CONF_LOAD_IDS] = sorted(load_ids)
                if disabled:
                    self._data[CONF_DISABLED_LOADS] = sorted(disabled)
                self._data[CONF_SCENE_IDS] = sorted(scene_ids)
                if self._data[CONF_SYSTEM_TYPE] == SYSTEM_JETSTREAM:
                    # Buttons from the .jts plus any hand-entered device IDs
                    # (which default to button 1).
                    button_pairs |= {(idx, 1) for idx in switch_ids}
                    # Validate button device numbers explicitly against the
                    # switch bound (don't rely on it equalling the load bound).
                    _check_range("switch", {d for d, _ in button_pairs}, system_type)
                    self._data[CONF_BUTTON_IDS] = [list(p) for p in sorted(button_pairs)]
                else:
                    self._data[CONF_SWITCH_IDS] = sorted(switch_ids)

                if load_names:
                    self._options[OPT_LOAD_NAMES] = load_names
                if scene_names:
                    self._options[OPT_SCENE_NAMES] = scene_names
                if switch_names:
                    self._options[OPT_SWITCH_NAMES] = switch_names
                # Only record loads we're actually exposing as on/off.
                nondimmable &= load_ids
                if nondimmable:
                    self._options[OPT_NONDIMMABLE_LOADS] = sorted(nondimmable)

                title = f"Centralite {SYSTEM_LABELS[self._data[CONF_SYSTEM_TYPE]]}"
                if self._data[CONF_SYSTEM_TYPE] == SYSTEM_JETSTREAM:
                    # New JetStream installs start on one device per unit.
                    self._data[CONF_TOPOLOGY] = TOPOLOGY_PER_DEVICE
                # Only once nothing else can reject the import.
                self._report_duplicate_device_ids(jts_duplicates)
                return self.async_create_entry(
                    title=title, data=self._data, options=self._options
                )
            except _ScanError:
                _LOGGER.warning("JetStream device scan failed", exc_info=True)
                errors["base"] = "scan_failed"
            except _IdRangeError:
                _LOGGER.warning("Import rejected: ID out of range", exc_info=True)
                errors["base"] = "ids_out_of_range"
            except ValueError:
                _LOGGER.exception("Import failed")
                errors["base"] = "import_failed"

        return self.async_show_form(
            step_id="import",
            data_schema=_import_schema(self._data[CONF_SYSTEM_TYPE]),
            errors=errors,
            description_placeholders={
                "system": SYSTEM_LABELS[self._data[CONF_SYSTEM_TYPE]],
            },
        )

    def _report_duplicate_device_ids(
        self, duplicates: dict[int, tuple[str, ...]]
    ) -> None:
        """Tell the user about Device IDs shared by several .jts records.

        Not an error: a Device ID is one address on the bridge, so one record
        was imported per ID. But the other record is a real device that Home
        Assistant cannot reach, which the user can only fix in JetStream
        Designer — so say which IDs and which names, as a Repairs notice that
        survives restarts, plus a log line.
        """
        issue_id = f"jts_duplicate_ids_{self.unique_id}"
        if not duplicates:
            # A clean re-import for the same bridge clears an earlier notice.
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        def label(name: str) -> str:
            return f'"{name}"' if name else "(unnamed)"

        lines = []
        for idx, names in sorted(duplicates.items()):
            kept, *dropped = names
            lines.append(
                f"- Device ID {idx}: imported {label(kept)}; "
                f"not imported: {', '.join(label(n) for n in dropped)}"
            )
        _LOGGER.warning(
            "The .jts file uses these Device IDs more than once; one record "
            "per ID was imported:\n%s",
            "\n".join(lines),
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="jts_duplicate_ids",
            translation_placeholders={
                "count": str(len(duplicates)),
                "details": "\n".join(lines),
            },
        )

    async def _scan_jetstream_names(self) -> dict[int, str]:
        """Open a temporary connection and discover device names via ^N.

        Runs only during setup (never on every boot) and can take up to a
        minute, since unconfigured device slots are silent and time out. Raises
        _ScanError if the bridge can't be reached.
        """
        protocol = JetStreamProtocol(
            self._data[CONF_PORT], baudrate=self._data[CONF_BAUD]
        )
        try:
            # Bound the open so a present-but-unresponsive adapter can't hang
            # the config flow forever.
            await asyncio.wait_for(protocol.connect(), timeout=10)
        except Exception as exc:  # serial failure or timeout opening the bridge
            await protocol.disconnect()  # close a partially-opened transport
            raise _ScanError(str(exc) or "timed out opening the bridge") from exc
        try:
            return await protocol.scan_device_names()
        except Exception as exc:
            # Includes a mid-scan disconnect (ProtocolError) — surface it as the
            # clean scan_failed form error instead of crashing the flow.
            raise _ScanError(str(exc) or "device scan failed") from exc
        finally:
            await protocol.disconnect()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CentraliteOptionsFlow()


class CentraliteOptionsFlow(OptionsFlow):
    """Settings, plus moving a JetStream entry to one device per unit.

    `self.config_entry` is provided by Home Assistant; assigning it here (as
    older code did) raises on HA 2024.11+ where it became a read-only property.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pending: list[DeviceAddition] = []
        self._removing: int | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        data = self.config_entry.data
        if user_input is None and data.get(CONF_SYSTEM_TYPE) == SYSTEM_JETSTREAM:
            if is_per_device(data):
                return self.async_show_menu(
                    step_id="init",
                    menu_options=[
                        "settings",
                        "add_device",
                        "add_batch",
                        "change_device",
                        "remove_device",
                    ],
                )
            # A JetStream entry still on the single-bridge layout can move to
            # one device per unit from here. Devices are edited after the move.
            return self.async_show_menu(
                step_id="init", menu_options=["settings", "organize"]
            )
        return await self._settings_form("init", user_input)

    # --- Adding, changing and removing JetStream devices -------------------

    def _save_devices(
        self,
        data: dict[str, Any],
        options: dict[str, Any],
        additions: list[DeviceAddition] | None = None,
    ) -> ConfigFlowResult:
        """Store new data and options; the entry's listener reloads it once."""
        from homeassistant.helpers import entity_registry as er

        entry = self.config_entry
        registry = er.async_get(self.hass)
        for addition in additions or []:
            # Asking for a load by ID also switches on a light the import had
            # created disabled. A light the user disabled is left alone.
            entity_id = registry.async_get_entity_id(
                "light", DOMAIN, f"{entry.entry_id}_load_{addition.device_id:03d}"
            )
            row = registry.async_get(entity_id) if entity_id else None
            if (
                row is not None
                and addition.device_type != TYPE_KEYPAD
                and row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            ):
                registry.async_update_entity(row.entity_id, disabled_by=None)
        self.hass.config_entries.async_update_entry(entry, data=data, options=options)
        return self.async_create_entry(title="", data=options)

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            addition = DeviceAddition(
                int(user_input["device_id"]),
                user_input["device_type"],
                frozenset(int(b) for b in user_input.get("buttons", [])),
                (user_input.get("name") or "").strip() or None,
            )
            try:
                merge_additions(
                    self.config_entry.data,
                    self.config_entry.options,
                    [*self._pending, addition],
                )
            except DeviceEditError as err:
                errors["base"] = err.code
            else:
                self._pending.append(addition)
                return await self.async_step_add_more()

        d = user_input or {}
        schema = vol.Schema(
            {
                vol.Required("device_id", default=d.get("device_id", 1)): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=MAX_DEVICE_ID, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    "device_type", default=d.get("device_type", TYPE_DIMMER)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[TYPE_DIMMER, TYPE_SWITCH, TYPE_KEYPAD],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="device_type",
                    )
                ),
                vol.Optional("buttons", default=d.get("buttons", [])): SelectSelector(
                    SelectSelectorConfig(
                        options=["1", "2", "3"],
                        multiple=True,
                        translation_key="keypad_button",
                    )
                ),
                vol.Optional("name", default=d.get("name", "")): _PORT_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="add_device", data_schema=schema, errors=errors
        )

    async def async_step_add_more(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="add_more",
            menu_options=["add_device", "save_devices"],
            description_placeholders={
                "count": str(len(self._pending)),
                "ids": ", ".join(f"{a.device_id:03d}" for a in self._pending),
            },
        )

    async def async_step_save_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            data, options = merge_additions(
                self.config_entry.data, self.config_entry.options, self._pending
            )
        except DeviceEditError:
            return self.async_abort(reason="device_list_changed")
        return self._save_devices(data, options, self._pending)

    async def async_step_add_batch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        line = ""
        if user_input is not None:
            try:
                additions = parse_batch(user_input.get("batch", ""))
                data, options = merge_additions(
                    self.config_entry.data, self.config_entry.options, additions
                )
            except DeviceEditError as err:
                errors["batch"] = err.code
                line = str(err.line) if err.line else ""
            else:
                return self._save_devices(data, options, additions)

        schema = vol.Schema(
            {
                vol.Required(
                    "batch", default=(user_input or {}).get("batch", "")
                ): _MULTILINE_SELECTOR
            }
        )
        return self.async_show_form(
            step_id="add_batch",
            data_schema=schema,
            errors=errors,
            description_placeholders={"line": line},
        )

    def _device_options(self, addresses: list[int]) -> list[dict[str, str]]:
        """Dropdown entries: the address, plus the imported name when known."""
        options = self.config_entry.options
        return [
            {
                "value": str(idx),
                "label": f"{idx:03d} {unit_name(options, idx) or ''}".strip(),
            }
            for idx in addresses
        ]

    async def async_step_change_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        loads = sorted(entry.data.get(CONF_LOAD_IDS, []))
        if not loads:
            return self.async_abort(reason="no_devices")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                options = change_load_type(
                    entry.data,
                    entry.options,
                    int(user_input["device"]),
                    dimmable=user_input["device_type"] == TYPE_DIMMER,
                )
            except DeviceEditError as err:
                errors["base"] = err.code
            else:
                return self.async_create_entry(title="", data=options)

        schema = vol.Schema(
            {
                vol.Required("device"): SelectSelector(
                    SelectSelectorConfig(
                        options=self._device_options(loads),
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required("device_type", default=TYPE_DIMMER): SelectSelector(
                    SelectSelectorConfig(
                        options=[TYPE_DIMMER, TYPE_SWITCH],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="device_type",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="change_device", data_schema=schema, errors=errors
        )

    async def async_step_remove_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        addresses = unit_addresses(self.config_entry.data)
        if not addresses:
            return self.async_abort(reason="no_devices")
        if user_input is not None:
            self._removing = int(user_input["device"])
            return await self.async_step_remove_confirm()
        schema = vol.Schema(
            {
                vol.Required("device"): SelectSelector(
                    SelectSelectorConfig(
                        options=self._device_options(addresses),
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove_device", data_schema=schema)

    def _unit_entity_rows(self, idx: int) -> list[Any]:
        """Registry rows of every entity that belongs to one unit address."""
        from homeassistant.helpers import entity_registry as er

        entry = self.config_entry
        registry = er.async_get(self.hass)
        tails = (f"_load_{idx:03d}",)
        marks = tuple(
            f"_button_{kind}_{idx:03d}_" for kind in ("event", "press", "hold")
        )
        return [
            row
            for row in er.async_entries_for_config_entry(registry, entry.entry_id)
            if row.platform == DOMAIN
            and (row.unique_id.endswith(tails) or any(m in row.unique_id for m in marks))
        ]

    async def async_step_remove_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        from .jetstream_devices import unit_identifier

        entry = self.config_entry
        idx = self._removing
        if idx is None:
            return await self.async_step_remove_device()
        rows = self._unit_entity_rows(idx)
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("confirm"):
                try:
                    data, options = remove_unit(entry.data, entry.options, idx)
                except DeviceEditError:
                    return self.async_abort(reason="device_list_changed")
                registry = er.async_get(self.hass)
                for row in rows:
                    registry.async_remove(row.entity_id)
                dev_reg = dr.async_get(self.hass)
                device = dev_reg.async_get_device_by_identifier(
                    unit_identifier(entry.entry_id, idx), entry.entry_id
                )
                if device is not None:
                    dev_reg.async_remove_device(device.id)
                return self._save_devices(data, options)
            errors["confirm"] = "confirm_required"

        return self.async_show_form(
            step_id="remove_confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): BooleanSelector()}
            ),
            errors=errors,
            description_placeholders={
                "device": f"{idx:03d} {unit_name(entry.options, idx) or ''}".strip(),
                "entities": "\n".join(f"- {row.entity_id}" for row in rows) or "-",
            },
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._settings_form("settings", user_input)

    async def _settings_form(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        # The clock-sync toggle only applies to Elegance (JetStream has no
        # clock command), so only offer/persist it there.
        is_elegance = self.config_entry.data.get(CONF_SYSTEM_TYPE) == SYSTEM_ELEGANCE

        if user_input is not None:
            # Merge with existing options (preserving name dicts)
            merged = dict(self.config_entry.options)
            merged[OPT_POLL_INTERVAL] = int(user_input[OPT_POLL_INTERVAL])
            if is_elegance:
                merged[OPT_SYNC_CLOCK_ON_CONNECT] = bool(
                    user_input.get(OPT_SYNC_CLOCK_ON_CONNECT, False)
                )
            return self.async_create_entry(title="", data=merged)

        cur = self.config_entry.options
        schema_dict: dict[Any, Any] = {
            vol.Optional(
                OPT_POLL_INTERVAL,
                default=cur.get(OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=3600, step=1, mode=NumberSelectorMode.BOX)
            ),
        }
        if is_elegance:
            schema_dict[
                vol.Optional(
                    OPT_SYNC_CLOCK_ON_CONNECT,
                    default=cur.get(OPT_SYNC_CLOCK_ON_CONNECT, False),
                )
            ] = BooleanSelector()
        return self.async_show_form(step_id=step_id, data_schema=vol.Schema(schema_dict))

    async def async_step_organize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Preview and confirm the move to one device per JetStream unit."""
        from .topology_migrate import async_prepare_units, build_preview

        entry = self.config_entry
        if entry.entry_id not in self.hass.data.get(DOMAIN, {}):
            return self.async_abort(reason="not_loaded")
        preview = build_preview(self.hass, entry)
        if preview.bridge_disabled:
            # Home Assistant re-derives "disabled by device" when an entity
            # changes device, which could switch entities back on.
            return self.async_abort(reason="bridge_disabled")

        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("confirm"):
                async_prepare_units(self.hass, entry)
                # One data change; the entry's update listener reloads it once.
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_TOPOLOGY: TOPOLOGY_PER_DEVICE}
                )
                return self.async_create_entry(title="", data=dict(entry.options))
            errors["confirm"] = "confirm_required"

        def _listing(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "-"

        return self.async_show_form(
            step_id="organize",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): BooleanSelector()}
            ),
            errors=errors,
            description_placeholders={
                "units": str(preview.units),
                "lights": str(preview.lights),
                "switches": str(len(preview.removed_switches)),
                "switch_list": _listing(preview.removed_switches),
                "references": _listing(preview.references),
                "renamed": _listing(preview.renamed_lights),
            },
        )
