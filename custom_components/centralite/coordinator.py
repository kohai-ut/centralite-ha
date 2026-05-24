"""DataUpdateCoordinator for the Centralite integration.

Push-primary: protocol callbacks invoke async_set_updated_data on every
load/switch/scene event. No update_interval (the DataUpdateCoordinator's
own polling path is unused). An optional safety-net poll runs ^G via
async_call_later — useful for catching loads that are not programmed for
spontaneous output in the bridge's Customer Options.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_SYSTEM_TYPE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    OPT_POLL_INTERVAL,
    SYSTEM_LABELS,
)
from .protocol import LoadEvent, SceneEvent, SwitchEvent

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant

    from .protocol import CentraliteProtocol

_LOGGER = logging.getLogger(__name__)


class CentraliteCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinates protocol state across all Centralite entities for one bridge."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        protocol: CentraliteProtocol,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{entry.entry_id[:8]}",
            update_interval=None,  # push-primary
        )
        self.config_entry = entry
        self.protocol = protocol
        # State shape:
        #   loads: {idx: {"on": bool, "level": int 0-99}}
        #   switches: {(idx, button): bool}  # button=0 for Elegance
        #   scenes: {idx: bool}              # commanded for Elegance, observed for JetStream
        self.data = {"loads": {}, "switches": {}, "scenes": {}}
        self._poll_unsub: CALLBACK_TYPE | None = None
        protocol.set_load_event_callback(self._on_load_event)
        protocol.set_switch_event_callback(self._on_switch_event)
        protocol.set_scene_event_callback(self._on_scene_event)

    @property
    def system_label(self) -> str:
        return SYSTEM_LABELS.get(self.config_entry.data[CONF_SYSTEM_TYPE], "Centralite")

    @property
    def device_name(self) -> str:
        return self.config_entry.title or f"Centralite {self.system_label}"

    async def async_init(self) -> None:
        """Open the bridge connection and prime initial state."""
        await self.protocol.connect()
        try:
            load_states = await self.protocol.get_all_load_states()
        except Exception:
            _LOGGER.warning(
                "Initial bulk load query failed; relying on push events",
                exc_info=True,
            )
        else:
            for idx, on in load_states.items():
                self.data["loads"][idx] = {"on": on, "level": 99 if on else 0}

        self._schedule_safety_poll()

    async def async_shutdown(self) -> None:
        """Cancel the safety poll and disconnect from the bridge."""
        if self._poll_unsub is not None:
            self._poll_unsub()
            self._poll_unsub = None
        await self.protocol.disconnect()

    async def activate_scene(self, idx: int) -> None:
        """Activate a scene and (for Elegance) reflect the commanded state."""
        await self.protocol.activate_scene(idx)
        if not self.protocol.supports_scene_push:
            self.data["scenes"][idx] = True
            self.async_set_updated_data(self.data)

    async def deactivate_scene(self, idx: int) -> None:
        await self.protocol.deactivate_scene(idx)
        if not self.protocol.supports_scene_push:
            self.data["scenes"][idx] = False
            self.async_set_updated_data(self.data)

    def _on_load_event(self, event: LoadEvent) -> None:
        cur = self.data["loads"].get(event.idx, {})
        cur["level"] = event.level
        cur["on"] = event.level > 0
        self.data["loads"][event.idx] = cur
        self.async_set_updated_data(self.data)

    def _on_switch_event(self, event: SwitchEvent) -> None:
        key = (event.idx, event.button)
        # Press / tap -> on, release -> off. Tap is momentary; we leave it on
        # until the next event clears it.
        self.data["switches"][key] = event.action != "release"
        self.async_set_updated_data(self.data)

    def _on_scene_event(self, event: SceneEvent) -> None:
        self.data["scenes"][event.idx] = event.active
        self.async_set_updated_data(self.data)

    def _schedule_safety_poll(self) -> None:
        interval = self.config_entry.options.get(OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        if not interval:
            return
        self._poll_unsub = async_call_later(self.hass, interval, self._safety_poll)

    async def _safety_poll(self, _now: Any) -> None:
        try:
            load_states = await self.protocol.get_all_load_states()
        except Exception:
            _LOGGER.exception("Safety poll failed")
        else:
            dirty = False
            for idx, on in load_states.items():
                cur = self.data["loads"].get(idx, {"on": False, "level": 0})
                if cur.get("on") != on:
                    cur = dict(cur)
                    cur["on"] = on
                    if not on:
                        cur["level"] = 0
                    elif cur.get("level", 0) == 0:
                        cur["level"] = 99  # on but level unknown
                    self.data["loads"][idx] = cur
                    dirty = True
            if dirty:
                self.async_set_updated_data(self.data)
        self._schedule_safety_poll()
