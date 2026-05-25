"""Config flow + options flow for Centralite."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
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
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
    SYSTEM_LABELS,
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
                    # Named keypad buttons -> switch entities. Mapping from the
                    # [letter][number] coordinate to the global switch index is
                    # derived (see parsers.elg._named_switches) and not yet
                    # hardware-verified.
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
    """Edit the safety-net poll interval post-setup.

    `self.config_entry` is provided by Home Assistant; assigning it here (as
    older code did) raises on HA 2024.11+ where it became a read-only property.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # Merge with existing options (preserving name dicts)
            merged = dict(self.config_entry.options)
            merged[OPT_POLL_INTERVAL] = int(user_input[OPT_POLL_INTERVAL])
            return self.async_create_entry(title="", data=merged)

        cur = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    OPT_POLL_INTERVAL,
                    default=cur.get(OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=3600, step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
