"""Switch platform for the Centralite integration.

Hosts three entity classes:
- CentraliteEleganceButtonSwitch: physical button on an Elegance system
- CentraliteJetStreamButtonSwitch: physical button (device + button index)
  on a JetStream system
- CentraliteSceneSwitch: a Centralite scene exposed as a stateful switch.
  On JetStream the state reflects real scene activation (SCN push). On
  Elegance the state reflects only what HA commanded — there is no scene
  push event from the Elegance bridge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity

from .const import (
    CONF_BUTTON_IDS,
    CONF_SCENE_IDS,
    CONF_SWITCH_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_SCENE_NAMES,
    OPT_SWITCH_NAMES,
    SYSTEM_JETSTREAM,
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
    entities: list[SwitchEntity] = []

    if entry.data[CONF_SYSTEM_TYPE] == SYSTEM_JETSTREAM:
        for pair in entry.data.get(CONF_BUTTON_IDS, []):
            device, button = pair[0], pair[1]
            entities.append(CentraliteJetStreamButtonSwitch(coordinator, device, button))
    else:
        for idx in entry.data.get(CONF_SWITCH_IDS, []):
            entities.append(CentraliteEleganceButtonSwitch(coordinator, idx))

    for idx in entry.data.get(CONF_SCENE_IDS, []):
        entities.append(CentraliteSceneSwitch(coordinator, idx))

    async_add_entities(entities)


class CentraliteEleganceButtonSwitch(CentraliteBaseEntity, SwitchEntity):
    """Physical button switch on an Elegance system."""

    def __init__(self, coordinator: CentraliteCoordinator, idx: int) -> None:
        super().__init__(coordinator)
        self._idx = idx
        self._attr_unique_id = f"{self._entry_id}_switch_{idx:03d}"
        names = coordinator.config_entry.options.get(OPT_SWITCH_NAMES, {})
        self._attr_name = names.get(str(idx), f"Switch {idx:03d}")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data["switches"].get((self._idx, 0), False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.press_switch(self._idx)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.release_switch(self._idx)


class CentraliteJetStreamButtonSwitch(CentraliteBaseEntity, SwitchEntity):
    """One button on a JetStream switch device."""

    def __init__(
        self,
        coordinator: CentraliteCoordinator,
        device_idx: int,
        button_idx: int,
    ) -> None:
        super().__init__(coordinator)
        self._device_idx = device_idx
        self._button_idx = button_idx
        self._attr_unique_id = f"{self._entry_id}_button_{device_idx:03d}_{button_idx:02d}"
        names = coordinator.config_entry.options.get(OPT_SWITCH_NAMES, {})
        key = f"{device_idx:03d}.{button_idx:02d}"
        self._attr_name = names.get(key, f"Switch {device_idx:03d} Button {button_idx}")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data["switches"].get((self._device_idx, self._button_idx), False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.tap_switch(self._device_idx, button=self._button_idx)

    async def async_turn_off(self, **kwargs: Any) -> None:
        # JetStream buttons are momentary; tap covers both edges. No-op here.
        pass


class CentraliteSceneSwitch(CentraliteBaseEntity, SwitchEntity):
    """A Centralite scene exposed as a stateful switch."""

    def __init__(self, coordinator: CentraliteCoordinator, idx: int) -> None:
        super().__init__(coordinator)
        self._idx = idx
        self._attr_unique_id = f"{self._entry_id}_scene_{idx:03d}"
        names = coordinator.config_entry.options.get(OPT_SCENE_NAMES, {})
        self._attr_name = names.get(str(idx), f"Scene {idx:03d}")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data["scenes"].get(self._idx, False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.activate_scene(self._idx)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.deactivate_scene(self._idx)
