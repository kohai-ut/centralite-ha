"""Light platform for the Centralite integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_DISABLED_LOADS,
    CONF_LOAD_IDS,
    DOMAIN,
    OPT_LOAD_NAMES,
    OPT_NONDIMMABLE_LOADS,
)
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
    # Loads flagged DIMMER=N in the .elg import are on/off relays, not dimmers.
    # Absent (manual ID entry, or no .elg): default to dimmable.
    nondimmable = set(coordinator.config_entry.options.get(OPT_NONDIMMABLE_LOADS, []))
    # Loads referenced by a scene/button but never named: created hidden so they
    # don't clutter the UI, but available to enable.
    disabled = set(entry.data.get(CONF_DISABLED_LOADS, []))
    async_add_entities(
        CentraliteLight(
            coordinator,
            idx,
            dimmable=idx not in nondimmable,
            enabled_default=idx not in disabled,
        )
        for idx in load_ids
    )


class CentraliteLight(CentraliteBaseEntity, LightEntity, RestoreEntity):
    """A single Centralite-controlled load.

    Dimmable loads use ColorMode.BRIGHTNESS; on/off relays use ColorMode.ONOFF
    so HA shows a simple toggle instead of a brightness slider that does
    nothing on a non-dimming relay.
    """

    def __init__(
        self,
        coordinator: CentraliteCoordinator,
        idx: int,
        *,
        dimmable: bool = True,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, unit_idx=idx)
        self._idx = idx
        self._dimmable = dimmable
        self._attr_entity_registry_enabled_default = enabled_default
        mode = ColorMode.BRIGHTNESS if dimmable else ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}
        self._attr_unique_id = f"{self._entry_id}_load_{idx:03d}"
        if self._on_unit:
            # The light is the main entity of its unit: the device carries the
            # imported name, so the light has none of its own.
            self._attr_name = None
        else:
            names = coordinator.config_entry.options.get(OPT_LOAD_NAMES, {})
            self._set_name(names.get(str(idx)), "load", id=f"{idx:03d}")

    async def async_added_to_hass(self) -> None:
        """Start from the last known state when the bridge has said nothing.

        A bridge with a bulk query (Elegance) has already reported every load
        by now, and that always wins. JetStream is asked one load at a time in
        the background, and a unit with a weak radio link may never answer;
        without this its light would read "off" after every restart or reload
        until someone used it. Any later report replaces the restored value.
        """
        await super().async_added_to_hass()
        loads = self.coordinator.data["loads"]
        if self._idx in loads:
            return
        last = await self.async_get_last_state()
        if last is None or last.state not in (STATE_ON, STATE_OFF):
            return
        if self._idx in loads:  # a report arrived while we were reading
            return
        on = last.state == STATE_ON
        level = 0
        if on:
            brightness = last.attributes.get(ATTR_BRIGHTNESS)
            if self._dimmable and isinstance(brightness, (int, float)):
                level = min(99, max(1, round(brightness / 255 * 99)))
            else:
                level = 99
        loads[self._idx] = {"on": on, "level": level}

    @property
    def _state(self) -> dict[str, Any]:
        return self.coordinator.data["loads"].get(self._idx, {})

    @property
    def is_on(self) -> bool:
        return self._state.get("on", False)

    @property
    def brightness(self) -> int | None:
        if not self._dimmable:
            return None  # on/off relay: no brightness in ONOFF color mode
        level = self._state.get("level", 0)
        if not level:
            return 0
        return min(255, int(level / 99 * 255))

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self._dimmable and ATTR_BRIGHTNESS in kwargs:
            # Floor at 1: turn_on with a low brightness must never round down to
            # level 0, which the bridge treats as OFF. A user asking for the
            # dimmest possible light should get the dimmest light, not darkness.
            level = max(1, round(kwargs[ATTR_BRIGHTNESS] / 255 * 99))
            await self.coordinator.protocol.set_load_level(self._idx, level)
        else:
            await self.coordinator.protocol.activate_load(self._idx)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.deactivate_load(self._idx)
