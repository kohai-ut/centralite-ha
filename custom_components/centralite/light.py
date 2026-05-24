"""Light platform for the Centralite integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity

from .const import CONF_LOAD_IDS, DOMAIN, OPT_LOAD_NAMES
from .entity import CentraliteBaseEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import CentraliteCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CentraliteCoordinator = hass.data[DOMAIN][entry.entry_id]
    load_ids: list[int] = entry.data.get(CONF_LOAD_IDS, [])
    async_add_entities(
        CentraliteLight(coordinator, idx) for idx in load_ids
    )


class CentraliteLight(CentraliteBaseEntity, LightEntity):
    """A single Centralite-controlled dimmable load."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: CentraliteCoordinator, idx: int) -> None:
        super().__init__(coordinator)
        self._idx = idx
        self._attr_unique_id = f"{self._entry_id}_load_{idx:03d}"
        names = coordinator.config_entry.options.get(OPT_LOAD_NAMES, {})
        self._attr_name = names.get(str(idx), f"Load {idx:03d}")

    @property
    def _state(self) -> dict[str, Any]:
        return self.coordinator.data["loads"].get(self._idx, {})

    @property
    def is_on(self) -> bool:
        return self._state.get("on", False)

    @property
    def brightness(self) -> int | None:
        level = self._state.get("level", 0)
        if not level:
            return 0
        return min(255, int(level / 99 * 255))

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            # Floor at 1: turn_on with a low brightness must never round down to
            # level 0, which the bridge treats as OFF. A user asking for the
            # dimmest possible light should get the dimmest light, not darkness.
            level = max(1, round(kwargs[ATTR_BRIGHTNESS] / 255 * 99))
            await self.coordinator.protocol.set_load_level(self._idx, level)
        else:
            await self.coordinator.protocol.activate_load(self._idx)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.deactivate_load(self._idx)
