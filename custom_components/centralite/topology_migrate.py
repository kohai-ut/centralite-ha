"""Moving an existing JetStream entry from the single-bridge layout to one
device per unit.

This module only previews the change and prepares the unit devices. The move
itself is one flag in the entry data: once it is set and the entry reloads,
entities report their unit device and Home Assistant re-links the same
registry rows, and setup removes the old button switches. Nothing here edits
an entity's device, rewrites an automation or touches a user's own names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_LOAD_IDS, DOMAIN, MANUFACTURER
from .jetstream_devices import unit_addresses, unit_identifier, unit_name

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


@dataclass
class TopologyPreview:
    """What moving one entry to the per-device layout will do."""

    units: int = 0
    lights: int = 0
    # Entity IDs of the button switches that will be removed.
    removed_switches: list[str] = field(default_factory=list)
    # Lights the user renamed: shown as "<device> <their name>" afterwards.
    renamed_lights: list[str] = field(default_factory=list)
    # Home Assistant automations and scripts that use a removed switch or
    # target the bridge device. Cannot see Node-RED, dashboards or templates.
    references: list[str] = field(default_factory=list)
    bridge_disabled: bool = False


def _bridge(hass: HomeAssistant, entry: ConfigEntry) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )


def _button_switch_rows(
    hass: HomeAssistant, entry: ConfigEntry
) -> list[er.RegistryEntry]:
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_button_"
    return [
        row
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
        if row.domain == "switch"
        and row.platform == DOMAIN
        and row.unique_id.startswith(prefix)
    ]


def _light_row(
    hass: HomeAssistant, entry: ConfigEntry, idx: int
) -> er.RegistryEntry | None:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "light", DOMAIN, f"{entry.entry_id}_load_{idx:03d}"
    )
    return registry.async_get(entity_id) if entity_id else None


def _references(
    hass: HomeAssistant, switch_ids: list[str], bridge_id: str | None
) -> list[str]:
    from homeassistant.components.automation import (
        automations_with_device,
        automations_with_entity,
    )
    from homeassistant.components.script import (
        scripts_with_device,
        scripts_with_entity,
    )

    found: set[str] = set()
    for entity_id in switch_ids:
        found.update(automations_with_entity(hass, entity_id))
        found.update(scripts_with_entity(hass, entity_id))
    if bridge_id:
        found.update(automations_with_device(hass, bridge_id))
        found.update(scripts_with_device(hass, bridge_id))
    return sorted(found)


@callback
def build_preview(hass: HomeAssistant, entry: ConfigEntry) -> TopologyPreview:
    """Describe the move without changing anything."""
    bridge = _bridge(hass, entry)
    switches = [row.entity_id for row in _button_switch_rows(hass, entry)]
    loads = [idx for idx in entry.data.get(CONF_LOAD_IDS, []) if isinstance(idx, int)]
    renamed = [
        row.entity_id
        for idx in loads
        if (row := _light_row(hass, entry, idx)) is not None and row.name
    ]
    return TopologyPreview(
        units=len(unit_addresses(entry.data)),
        lights=len(loads),
        removed_switches=switches,
        renamed_lights=renamed,
        references=_references(hass, switches, bridge.id if bridge else None),
        bridge_disabled=bridge is not None and bridge.disabled_by is not None,
    )


@callback
def async_prepare_units(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create the unit devices so each keeps the place its light had.

    A light with its own area hands that area to its new device; every other
    unit starts in the bridge's area. The bridge's labels are copied once.
    Only a device created here is given an area or labels, so running this
    again never overwrites what the user has since changed.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    bridge = _bridge(hass, entry)
    if bridge is None:
        return

    for idx in unit_addresses(entry.data):
        identifier = unit_identifier(entry.entry_id, idx)
        if dev_reg.async_get_device_by_identifier(identifier, entry.entry_id):
            continue
        light = _light_row(hass, entry, idx)
        area_id = (light.area_id if light else None) or bridge.area_id

        if name := unit_name(entry.options, idx):
            naming = {"name": name}
        else:
            naming = {
                "translation_key": "unit",
                "translation_placeholders": {"id": f"{idx:03d}"},
            }
        device = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={identifier},
            manufacturer=MANUFACTURER,
            via_device_id=bridge.id,
            **naming,
        )
        dev_reg.async_update_device(
            device.id, area_id=area_id, labels=set(bridge.labels)
        )
        if light is not None:
            # A light with no name of its own takes its device's area, so an
            # override equal to it is redundant. Its effective area is unchanged.
            if light.area_id is not None and light.area_id == area_id:
                ent_reg.async_update_entity(light.entity_id, area_id=None)
            # Keep an older voice-assistant name as an alias where Home
            # Assistant provides for it (2026.11+).
            preserve = getattr(er, "async_preserve_compat_name_as_alias", None)
            if preserve is not None:
                preserve(hass, light.entity_id)
