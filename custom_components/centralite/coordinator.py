"""DataUpdateCoordinator for the Centralite integration.

Push-primary: protocol callbacks invoke async_set_updated_data on every
load/switch/scene event. No update_interval (the DataUpdateCoordinator's
own polling path is unused). An optional safety-net poll runs ^G via
async_call_later — useful for catching loads that are not programmed for
spontaneous output in the bridge's Customer Options.

Bridges with no bulk command (JetStream) instead get a one-time background
refresh after each connect: every configured load is asked for its level with
^F, one at a time, so Home Assistant doesn't show a light as off just because
it hasn't changed since the restart. Setup and reconnect never wait on it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_DEVICE_ID, CONF_TYPE
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_LOAD_IDS,
    CONF_SUBTYPE,
    CONF_SYSTEM_TYPE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    EVENT_BUTTON,
    OPT_POLL_INTERVAL,
    OPT_SYNC_CLOCK_ON_CONNECT,
    SYSTEM_LABELS,
    button_subtype,
)
from .protocol import LoadEvent, SceneEvent, SwitchEvent
from .protocol.common import ProtocolError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant

    from .protocol import CentraliteProtocol

_LOGGER = logging.getLogger(__name__)

# After an unexpected disconnect, retry the connection on an exponential backoff
# (a transient USB/serial blip shouldn't leave entities dead until a manual
# reload). Start quick, cap so we don't hammer a permanently-gone bridge.
RECONNECT_INITIAL_DELAY = 5  # seconds
RECONNECT_MAX_DELAY = 300  # seconds

# Per-load refresh (bridges with supports_per_load_query, i.e. JetStream ^F).
# It runs in a background task, so these bound serial traffic, not startup.
#
# Per-load wait. The bridge has to ask the device over the wireless mesh
# before it can answer on RS-232, so replies are not instant; this matches the
# protocol's normal command timeout. A device that doesn't answer (a keypad
# with no load, say) costs one full timeout.
LOAD_REFRESH_TIMEOUT = 2.0  # seconds
# If this many queries in a row go unanswered from the very start, the bridge
# isn't replying at all: stop instead of timing out on every remaining load.
LOAD_REFRESH_PROBE_COUNT = 5
# Hard cap on one refresh (first pass plus the retry pass).
LOAD_REFRESH_BUDGET = 300.0  # seconds


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
        self._reconnect_unsub: CALLBACK_TYPE | None = None
        self._reconnect_delay = RECONNECT_INITIAL_DELAY
        self._reconnecting = False
        self._shutting_down = False
        self._load_refresh_task: asyncio.Task[None] | None = None
        protocol.set_load_event_callback(self._on_load_event)
        protocol.set_switch_event_callback(self._on_switch_event)
        protocol.set_scene_event_callback(self._on_scene_event)
        protocol.set_disconnect_callback(self._on_disconnect)

    @property
    def system_label(self) -> str:
        return SYSTEM_LABELS.get(self.config_entry.data[CONF_SYSTEM_TYPE], "Centralite")

    @property
    def device_name(self) -> str:
        return self.config_entry.title or f"Centralite {self.system_label}"

    async def async_init(self) -> None:
        """Open the bridge connection and prime initial state.

        Bridges with a bulk-state command (Elegance ^G) are primed once at
        startup and then polled as a safety net. Bridges without one
        (JetStream) skip both — calling get_all_load_states there would just
        time out on every attempt — and instead have each load's level read
        in a background task that this method starts but never waits for.
        """
        await self.protocol.connect()
        await self._prime_loads()
        await self._sync_clock_if_enabled()
        self._schedule_safety_poll()
        self._start_load_refresh()

    async def _sync_clock_if_enabled(self) -> None:
        """Set the bridge clock to HA's local time, if the option is enabled.

        Called only after a successful connect (from async_init and after a
        reconnect), so it is inherently free of the boot race that an
        automation triggered on 'HA start' would face — the serial link is
        already up. Swallows its own errors: a clock-set failure must never
        block setup or abort a reconnect. Elegance-only (supports_clock).
        """
        if not self.protocol.supports_clock:
            return
        if not self.config_entry.options.get(OPT_SYNC_CLOCK_ON_CONNECT, False):
            return
        if not self.protocol.connected:
            return  # link dropped during connect/prime — don't write a dead port
        try:
            now = dt_util.now()
            await self.protocol.set_clock(now)
            _LOGGER.debug("Synced Centralite bridge clock to %s", now.isoformat())
        except Exception:
            _LOGGER.warning("Failed to sync Centralite bridge clock", exc_info=True)

    async def _prime_loads(self) -> None:
        """Seed initial load state from a bulk query, where the bridge supports it."""
        if not self.protocol.supports_bulk_query:
            return
        try:
            load_states = await self.protocol.get_all_load_states()
        except Exception:
            _LOGGER.warning(
                "Bulk load query failed; relying on push events", exc_info=True
            )
        else:
            for idx, on in load_states.items():
                self.data["loads"][idx] = {"on": on, "level": 99 if on else 0}

    def _start_load_refresh(self) -> None:
        """Start reading every configured load's level in the background.

        Only for bridges that must be queried one load at a time (JetStream).
        Never awaited by setup or reconnect: one silent device costs a full
        LOAD_REFRESH_TIMEOUT, and that must not delay Home Assistant startup
        or hold entities unavailable. Replaces any refresh already running.
        """
        if not self.protocol.supports_per_load_query:
            return
        if self._shutting_down or not self.protocol.connected:
            return
        load_ids = list(self.config_entry.data.get(CONF_LOAD_IDS, []))
        if not load_ids:
            return
        self._cancel_load_refresh()
        # eager_start=False: the task must not run any part of the refresh
        # inline, inside the setup/reconnect call that starts it.
        self._load_refresh_task = self.config_entry.async_create_background_task(
            self.hass,
            self._refresh_loads(load_ids),
            name=f"{DOMAIN}-load-refresh-{self.config_entry.entry_id[:8]}",
            eager_start=False,
        )

    def _cancel_load_refresh(self) -> None:
        if self._load_refresh_task is not None:
            self._load_refresh_task.cancel()
            self._load_refresh_task = None

    async def _refresh_loads(self, load_ids: list[int]) -> None:
        """Ask each load for its level; the DEV replies update state.

        The reply to ^F arrives as a normal DEV line, which the protocol also
        hands to _on_load_event — so there is nothing to write here. This
        just walks the loads, bounded three ways: a per-load timeout, an
        early stop when the bridge answers nothing at all, and a total time
        budget. Loads that stay silent get one retry pass at the end, which
        covers a reply that was merely slow or dropped on the mesh.
        """
        task = asyncio.current_task()
        deadline = self.hass.loop.time() + LOAD_REFRESH_BUDGET
        answered = 0
        silent: list[int] = []
        stopped: str | None = None
        try:
            for asked, idx in enumerate(load_ids, start=1):
                if stopped := self._load_refresh_stop_reason(deadline):
                    break
                if await self._query_load(idx):
                    answered += 1
                    continue
                silent.append(idx)
                if answered == 0 and asked >= LOAD_REFRESH_PROBE_COUNT:
                    stopped = "the bridge is not answering level queries"
                    break

            if stopped is None and silent:
                retry, silent = silent, []
                for idx in retry:
                    if stopped := self._load_refresh_stop_reason(deadline):
                        break
                    if await self._query_load(idx):
                        answered += 1
                    else:
                        silent.append(idx)
        except Exception:
            # A background task has no caller to report to; without this an
            # unexpected error would vanish. Push events still work.
            _LOGGER.exception("Load level refresh failed unexpectedly")
            return
        finally:
            if self._load_refresh_task is task:
                self._load_refresh_task = None

        _LOGGER.info(
            "Load level refresh: %s of %s configured loads answered%s",
            answered,
            len(load_ids),
            f"; stopped early because {stopped}" if stopped else "",
        )
        if silent:
            _LOGGER.debug("Loads that did not answer a level query: %s", silent)

    def _load_refresh_stop_reason(self, deadline: float) -> str | None:
        """Why the refresh must stop now, or None to keep going."""
        if self._shutting_down:
            return "the integration is shutting down"
        if not self.protocol.connected:
            return "the bridge connection was lost"
        if self.hass.loop.time() >= deadline:
            return "the time budget ran out"
        return None

    async def _query_load(self, idx: int) -> bool:
        """Ask one load for its level. False if it didn't answer."""
        try:
            await self.protocol.get_load_level(idx, timeout=LOAD_REFRESH_TIMEOUT)
        except (ProtocolError, OSError, ValueError) as err:
            # Expected for a device with no load to report; not worth a warning.
            _LOGGER.debug("No level reply from load %03d: %s", idx, err)
            return False
        return True

    async def async_shutdown(self) -> None:
        """Cancel timers and disconnect from the bridge."""
        self._shutting_down = True
        self._cancel_poll()
        self._cancel_load_refresh()
        if self._reconnect_unsub is not None:
            self._reconnect_unsub()
            self._reconnect_unsub = None
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
        _LOGGER.debug("load event: idx=%s level=%s", event.idx, event.level)
        cur = self.data["loads"].get(event.idx, {})
        cur["level"] = event.level
        cur["on"] = event.level > 0
        self.data["loads"][event.idx] = cur
        self.async_set_updated_data(self.data)

    def _on_switch_event(self, event: SwitchEvent) -> None:
        _LOGGER.debug(
            "switch event: idx=%s button=%s action=%s",
            event.idx,
            event.button,
            event.action,
        )
        key = (event.idx, event.button)
        # Press / tap -> on, release -> off. Tap is momentary; we leave it on
        # until the next event clears it.
        self.data["switches"][key] = event.action != "release"
        self.async_set_updated_data(self.data)
        self._fire_button_event(event)

    @callback
    def _fire_button_event(self, event: SwitchEvent) -> None:
        """Fire a bus event for this physical button activity.

        Drives device triggers (device_trigger.py) so automations can run on a
        physical keypad press/tap/release. The event carries the bridge's HA
        device id plus the action and a button subtype that device_trigger's
        enumeration matches.
        """
        device_id = self._bridge_device_id()
        if device_id is None:
            return  # device not registered yet (no entities added); nothing to target
        self.hass.bus.async_fire(
            EVENT_BUTTON,
            {
                CONF_DEVICE_ID: device_id,
                CONF_TYPE: event.action,
                CONF_SUBTYPE: button_subtype(event.idx, event.button),
            },
        )

    def _bridge_device_id(self) -> str | None:
        """HA device-registry id of the bridge device.

        Looked up fresh each time (a registry lookup is just a dict hit) so a
        deleted-and-recreated device can't leave us firing events at a dead id.
        """
        from homeassistant.helpers import device_registry as dr

        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, self.config_entry.entry_id)}
        )
        return device.id if device else None

    def _on_scene_event(self, event: SceneEvent) -> None:
        _LOGGER.debug("scene event: idx=%s active=%s", event.idx, event.active)
        self.data["scenes"][event.idx] = event.active
        self.async_set_updated_data(self.data)

    @callback
    def _on_disconnect(self, error: Exception | None) -> None:
        """Mark entities unavailable on a link drop and start reconnecting.

        async_set_update_error flips last_update_success to False so every
        entity goes unavailable instead of showing stale state, then we retry
        the connection on a backoff (see _try_reconnect).
        """
        _LOGGER.warning("Centralite bridge connection lost: %s", error)
        self._cancel_poll()  # poll would just raise against the dead link
        self._cancel_load_refresh()  # so would the remaining level queries
        self.async_set_update_error(
            UpdateFailed(f"Centralite bridge connection lost: {error}")
        )
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        # Guarded against shutdown, an in-flight attempt, and an already-armed
        # timer so a flurry of disconnects can't stack reconnect loops.
        if self._shutting_down or self._reconnecting or self._reconnect_unsub is not None:
            return
        _LOGGER.debug("Scheduling reconnect in %ss", self._reconnect_delay)
        self._reconnect_unsub = async_call_later(
            self.hass, self._reconnect_delay, self._try_reconnect
        )

    async def _try_reconnect(self, _now: Any) -> None:
        self._reconnect_unsub = None
        if self._shutting_down:
            return
        self._reconnecting = True
        success = False
        try:
            await self.protocol.disconnect()  # tear down the dead transport
            await self.protocol.connect()
            await self._prime_loads()
            # Treat as success only if the link is still up — it may have
            # dropped again during the open/prime window.
            success = self.protocol.connected
        except Exception as err:  # noqa: BLE001 - any failure means "retry later"
            _LOGGER.debug("Reconnect attempt failed (%s)", err)
        finally:
            self._reconnecting = False

        # Re-check after the awaits: the entry may have been unloaded mid-attempt.
        if self._shutting_down:
            if success:
                await self.protocol.disconnect()
            return
        if success:
            _LOGGER.info("Reconnected to Centralite bridge")
            self._reconnect_delay = RECONNECT_INITIAL_DELAY
            self.async_set_updated_data(self.data)  # clears error -> available
            await self._sync_clock_if_enabled()
            # The clock sync awaits a write; the link (or the entry) may have
            # gone away meanwhile. Re-check before arming the poll so we don't
            # schedule a ^G against a now-dead link (a fresh _on_disconnect has
            # already scheduled its own reconnect in that case).
            if self._shutting_down or not self.protocol.connected:
                return
            self._schedule_safety_poll()
            self._start_load_refresh()
        else:
            self._reconnect_delay = min(self._reconnect_delay * 2, RECONNECT_MAX_DELAY)
            self._schedule_reconnect()

    def _cancel_poll(self) -> None:
        if self._poll_unsub is not None:
            self._poll_unsub()
            self._poll_unsub = None

    def _schedule_safety_poll(self) -> None:
        # The safety poll uses the bulk-state command. Push-only bridges
        # (JetStream) have none, so there is nothing to poll — skip it rather
        # than scheduling a callback that raises every interval.
        self._cancel_poll()
        if self._shutting_down or not self.protocol.supports_bulk_query:
            return
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
