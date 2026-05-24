"""Base entity classes for the Centralite integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from .coordinator import CentraliteCoordinator


class CentraliteBaseEntity(CoordinatorEntity["CentraliteCoordinator"]):
    """Common base: shared DeviceInfo and has_entity_name."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CentraliteCoordinator) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._entry_id = entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=coordinator.system_label,
        )
