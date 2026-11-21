"""Base entity classes for the Centralite integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .jetstream_devices import is_per_device, unit_identifier, unit_name

if TYPE_CHECKING:
    from .coordinator import CentraliteCoordinator


class CentraliteBaseEntity(CoordinatorEntity["CentraliteCoordinator"]):
    """Common base: DeviceInfo and has_entity_name.

    Entities belong to the bridge device unless the entry uses the JetStream
    per-device layout AND the entity names the physical unit it lives on
    (``unit_idx``). Scenes and every Elegance entity never pass a unit, so
    they stay on the bridge.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CentraliteCoordinator, *, unit_idx: int | None = None
    ) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._entry_id = entry.entry_id
        self._on_unit = unit_idx is not None and is_per_device(entry.data)
        if unit_idx is not None and self._on_unit:
            self._attr_device_info = self._unit_device_info(unit_idx)
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                name=coordinator.device_name,
                manufacturer=MANUFACTURER,
                model=coordinator.system_label,
            )

    def _set_name(self, stored: str | None, key: str, **placeholders: str) -> None:
        """Use the imported name, or a translated fallback when there is none.

        The fallback text lives in the translation file, not here, so it can
        be translated. It reads the same as before in English.
        """
        if stored:
            self._attr_name = stored
        else:
            self._attr_translation_key = key
            self._attr_translation_placeholders = placeholders

    def _unit_device_info(self, idx: int) -> DeviceInfo:
        """One physical JetStream unit, reached through the bridge."""
        entry = self.coordinator.config_entry
        info = DeviceInfo(
            identifiers={unit_identifier(entry.entry_id, idx)},
            manufacturer=MANUFACTURER,
        )
        # The imported name, verbatim. A unit the import did not name gets a
        # translated "Device {id}" rather than text built here.
        if name := unit_name(entry.options, idx):
            info["name"] = name
        else:
            info["translation_key"] = "unit"
            info["translation_placeholders"] = {"id": f"{idx:03d}"}
        if bridge_id := self.coordinator.bridge_device_id():
            info["via_device_id"] = bridge_id
        return info
