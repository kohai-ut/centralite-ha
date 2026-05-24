"""Config flow + options flow for Centralite."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
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
    CONF_LOAD_IDS,
    CONF_PORT,
    CONF_SCENE_IDS,
    CONF_SWITCH_IDS,
    CONF_SYSTEM_TYPE,
    DEFAULT_BAUD,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    OPT_LOAD_NAMES,
    OPT_POLL_INTERVAL,
    OPT_SCENE_NAMES,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
    SYSTEM_LABELS,
)
from .parsers.elg import parse_csv_ids, parse_elg
from .protocol.common import MAX_LOADS as ELEGANCE_MAX_LOADS
from .protocol.common import MAX_SWITCHES as ELEGANCE_MAX_SWITCHES
from .protocol.elegance import MAX_SCENES as ELEGANCE_MAX_SCENES
from .protocol.jetstream import (
    JETSTREAM_MAX_LOADS,
    JETSTREAM_MAX_SCENES,
    JETSTREAM_MAX_SWITCHES,
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


def _import_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional("elg_text", default=""): _MULTILINE_SELECTOR,
            vol.Optional("load_ids_csv", default=""): _CSV_SELECTOR,
            vol.Optional("scene_ids_csv", default=""): _CSV_SELECTOR,
            vol.Optional("switch_ids_csv", default=""): _CSV_SELECTOR,
        }
    )


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
                load_ids: set[int] = set()
                scene_ids: set[int] = set()
                switch_ids: set[int] = set()

                elg_text = user_input.get("elg_text", "").strip()
                if elg_text:
                    cfg = parse_elg(elg_text)
                    if not cfg.loads and not cfg.scenes:
                        # Non-empty paste that yielded nothing: not a recognized
                        # Elegance .elg file. Most likely a JetStream .jts export
                        # (not supported yet) or the wrong file. Tell the user
                        # rather than silently importing zero devices.
                        raise ValueError("unrecognized import text")
                    for idx, name in cfg.loads.items():
                        load_ids.add(idx)
                        if name:
                            load_names[str(idx)] = name
                    for idx, name in cfg.scenes.items():
                        scene_ids.add(idx)
                        if name:
                            scene_names[str(idx)] = name

                load_ids.update(parse_csv_ids(user_input.get("load_ids_csv", "")))
                scene_ids.update(parse_csv_ids(user_input.get("scene_ids_csv", "")))
                switch_ids.update(parse_csv_ids(user_input.get("switch_ids_csv", "")))

                system_type = self._data[CONF_SYSTEM_TYPE]
                _check_range("load", load_ids, system_type)
                _check_range("scene", scene_ids, system_type)
                _check_range("switch", switch_ids, system_type)

                self._data[CONF_LOAD_IDS] = sorted(load_ids)
                self._data[CONF_SCENE_IDS] = sorted(scene_ids)
                if self._data[CONF_SYSTEM_TYPE] == SYSTEM_JETSTREAM:
                    self._data[CONF_BUTTON_IDS] = [[idx, 1] for idx in sorted(switch_ids)]
                else:
                    self._data[CONF_SWITCH_IDS] = sorted(switch_ids)

                if load_names:
                    self._options[OPT_LOAD_NAMES] = load_names
                if scene_names:
                    self._options[OPT_SCENE_NAMES] = scene_names

                title = f"Centralite {SYSTEM_LABELS[self._data[CONF_SYSTEM_TYPE]]}"
                return self.async_create_entry(
                    title=title, data=self._data, options=self._options
                )
            except _IdRangeError:
                _LOGGER.warning("Import rejected: ID out of range", exc_info=True)
                errors["base"] = "ids_out_of_range"
            except ValueError:
                _LOGGER.exception("Import failed")
                errors["base"] = "import_failed"

        return self.async_show_form(
            step_id="import",
            data_schema=_import_schema(),
            errors=errors,
            description_placeholders={
                "system": SYSTEM_LABELS[self._data[CONF_SYSTEM_TYPE]],
            },
        )

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
