"""Coordinator tests: push handling, scene state, safety poll, disconnect.

These exercise the push-primary state machine that has no standalone coverage:

    protocol push ──> _on_load_event ───┐
                  ──> _on_switch_event ──┼─> coordinator.data ─> entities
                  ──> _on_scene_event ───┘
    activate_scene ─(no scene push)─> commanded state written directly
    reader death ──> _on_disconnect ─> last_update_success = False
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.centralite.const import (
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_POLL_INTERVAL,
    OPT_SYNC_CLOCK_ON_CONNECT,
    SYSTEM_ELEGANCE,
)
from custom_components.centralite.coordinator import CentraliteCoordinator
from custom_components.centralite.protocol import LoadEvent, SceneEvent, SwitchEvent

from .conftest import FakeProtocol


async def _advance(hass, seconds: int = 400) -> None:
    """Jump the clock far enough to fire any pending reconnect timer (backoff cap 300s)."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def _entry(hass, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SYSTEM_TYPE: SYSTEM_ELEGANCE, "port": "/dev/ttyUSB0", "baud": 19200},
        options=options or {},
        title="Test Bridge",
    )
    entry.add_to_hass(hass)
    return entry


async def test_async_init_primes_from_bulk_query(hass):
    proto = FakeProtocol(supports_bulk=True, bulk_loads={1: True, 2: False})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    assert coord.data["loads"][1] == {"on": True, "level": 99}
    assert coord.data["loads"][2] == {"on": False, "level": 0}
    await coord.async_shutdown()


async def test_async_init_skips_bulk_for_push_only(hass):
    """Push-only bridge (JetStream) must not call/await the bulk query."""
    proto = FakeProtocol(supports_bulk=False)
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()  # must not raise even though get_all_load_states would
    assert coord.data["loads"] == {}
    assert coord._poll_unsub is None  # no safety poll scheduled
    await coord.async_shutdown()


async def test_safety_poll_scheduled_only_with_bulk(hass):
    proto = FakeProtocol(supports_bulk=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass, {OPT_POLL_INTERVAL: 300}), proto)
    await coord.async_init()
    assert coord._poll_unsub is not None
    await coord.async_shutdown()
    assert coord._poll_unsub is None


async def test_poll_interval_zero_disables_poll(hass):
    proto = FakeProtocol(supports_bulk=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass, {OPT_POLL_INTERVAL: 0}), proto)
    await coord.async_init()
    assert coord._poll_unsub is None
    await coord.async_shutdown()


async def test_load_push_updates_state(hass):
    proto = FakeProtocol(bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    proto.load_cb(LoadEvent(idx=5, level=42))
    assert coord.data["loads"][5] == {"level": 42, "on": True}
    proto.load_cb(LoadEvent(idx=5, level=0))
    assert coord.data["loads"][5] == {"level": 0, "on": False}
    await coord.async_shutdown()


async def test_switch_push_press_then_release(hass):
    proto = FakeProtocol(bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    proto.switch_cb(SwitchEvent(idx=3, action="press", board=0))
    assert coord.data["switches"][(3, 0)] is True
    proto.switch_cb(SwitchEvent(idx=3, action="release", board=0))
    assert coord.data["switches"][(3, 0)] is False
    await coord.async_shutdown()


async def test_switch_event_logged_at_debug(hass, caplog):
    """A push event logs its decoded form, so watching the log during testing
    shows which device/button/action the bridge reported."""
    import logging

    proto = FakeProtocol(bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    with caplog.at_level(logging.DEBUG, logger="custom_components.centralite.coordinator"):
        proto.switch_cb(SwitchEvent(idx=44, action="tap", button=1))
    assert "switch event: idx=44 button=1 action=tap" in caplog.text
    await coord.async_shutdown()


async def test_scene_commanded_state_when_no_push(hass):
    """Elegance has no scene push: activate_scene reflects commanded state."""
    proto = FakeProtocol(supports_scene_push=False, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    await coord.activate_scene(7)
    assert coord.data["scenes"][7] is True
    assert ("activate_scene", 7) in proto.calls
    await coord.deactivate_scene(7)
    assert coord.data["scenes"][7] is False
    await coord.async_shutdown()


async def test_scene_state_waits_for_push_when_supported(hass):
    """JetStream pushes SCN: activate_scene must NOT pre-set commanded state."""
    proto = FakeProtocol(supports_scene_push=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    await coord.activate_scene(7)
    assert 7 not in coord.data["scenes"]  # no optimistic write
    proto.scene_cb(SceneEvent(idx=7, active=True))
    assert coord.data["scenes"][7] is True
    await coord.async_shutdown()


async def test_disconnect_marks_update_failed(hass):
    proto = FakeProtocol(bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    assert coord.last_update_success is True
    proto.disconnect_cb(OSError("cable pulled"))
    assert coord.last_update_success is False
    await coord.async_shutdown()


async def test_sync_clock_on_connect_when_enabled(hass):
    proto = FakeProtocol(supports_clock=True, bulk_loads={})
    coord = CentraliteCoordinator(
        hass, _entry(hass, {OPT_SYNC_CLOCK_ON_CONNECT: True}), proto
    )
    await coord.async_init()
    assert [c for c in proto.calls if c[0] == "set_clock"]


async def test_no_sync_clock_when_disabled(hass):
    """Default (option absent) must not touch the bridge clock."""
    proto = FakeProtocol(supports_clock=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    assert not [c for c in proto.calls if c[0] == "set_clock"]


async def test_no_sync_clock_when_unsupported(hass):
    """Even with the option on, a clockless bridge (JetStream) is never written
    to — set_clock would raise NotImplementedError there."""
    proto = FakeProtocol(supports_clock=False, supports_bulk=False)
    coord = CentraliteCoordinator(
        hass, _entry(hass, {OPT_SYNC_CLOCK_ON_CONNECT: True}), proto
    )
    await coord.async_init()
    assert not [c for c in proto.calls if c[0] == "set_clock"]


async def test_sync_clock_again_on_reconnect(hass):
    proto = FakeProtocol(supports_clock=True, bulk_loads={})
    coord = CentraliteCoordinator(
        hass, _entry(hass, {OPT_SYNC_CLOCK_ON_CONNECT: True}), proto
    )
    await coord.async_init()
    assert len([c for c in proto.calls if c[0] == "set_clock"]) == 1
    proto.disconnect_cb(OSError("cable pulled"))
    await _advance(hass)  # reconnect timer fires -> re-sync
    assert proto.connected is True
    assert len([c for c in proto.calls if c[0] == "set_clock"]) == 2
    await coord.async_shutdown()


async def test_sync_clock_failure_does_not_break_setup(hass):
    """A clock-set error must be swallowed, not abort async_init."""

    async def boom(_dt):
        raise OSError("write failed")

    proto = FakeProtocol(supports_clock=True, bulk_loads={})
    proto.set_clock = boom  # type: ignore[method-assign]
    coord = CentraliteCoordinator(
        hass, _entry(hass, {OPT_SYNC_CLOCK_ON_CONNECT: True}), proto
    )
    await coord.async_init()  # must not raise
    assert proto.connected is True


async def test_reconnect_clock_failure_does_not_break_reconnect(hass):
    """A set_clock error on the reconnect path is swallowed: the reconnect
    still completes (entities available) and the safety poll is armed."""

    async def boom(_dt):
        raise OSError("write failed")  # link still up, just a bad write

    proto = FakeProtocol(supports_clock=True, supports_bulk=True, bulk_loads={})
    coord = CentraliteCoordinator(
        hass, _entry(hass, {OPT_SYNC_CLOCK_ON_CONNECT: True, OPT_POLL_INTERVAL: 300}), proto
    )
    await coord.async_init()
    proto.set_clock = boom  # type: ignore[method-assign]
    proto.disconnect_cb(OSError("drop"))
    await _advance(hass)  # reconnect: connect ok, clock-sync raises, swallowed
    assert proto.connected is True
    assert coord.last_update_success is True  # reconnect succeeded
    assert coord._poll_unsub is not None  # poll re-armed despite clock failure
    await coord.async_shutdown()


async def test_reconnect_link_drop_mid_clock_sync_does_not_arm_poll(hass):
    """If the link drops *during* the clock write on reconnect, we must not
    arm the safety poll against the now-dead link (the drop's own reconnect
    handles recovery)."""
    proto = FakeProtocol(supports_clock=True, supports_bulk=True, bulk_loads={})
    coord = CentraliteCoordinator(
        hass, _entry(hass, {OPT_SYNC_CLOCK_ON_CONNECT: True, OPT_POLL_INTERVAL: 300}), proto
    )
    await coord.async_init()

    async def drop_then_fail(_dt):
        proto.connected = False
        coord._on_disconnect(OSError("dropped mid-write"))
        raise OSError("write failed")

    proto.set_clock = drop_then_fail  # type: ignore[method-assign]
    proto.disconnect_cb(OSError("initial drop"))
    await _advance(hass)  # reconnect connects, then loses link during clock sync
    assert coord._poll_unsub is None  # no poll armed against the dead link
    await coord.async_shutdown()


async def test_reconnect_restores_availability(hass):
    proto = FakeProtocol(supports_bulk=True, bulk_loads={1: True})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    proto.disconnect_cb(OSError("cable pulled"))
    assert coord.last_update_success is False
    await _advance(hass)  # reconnect timer fires
    assert proto.connected is True
    assert coord.last_update_success is True  # entities available again
    await coord.async_shutdown()


async def test_reconnect_retries_until_success(hass):
    proto = FakeProtocol(supports_bulk=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    proto.connect_error = OSError("still down")  # reconnect attempts fail
    proto.disconnect_cb(OSError("drop"))

    await _advance(hass)  # attempt 1 fails
    assert coord.last_update_success is False
    attempts = proto.connect_count
    await _advance(hass)  # attempt 2 fails -> proves it keeps retrying
    assert proto.connect_count > attempts
    assert coord.last_update_success is False

    proto.connect_error = None  # link comes back
    await _advance(hass)  # next attempt succeeds
    assert coord.last_update_success is True
    await coord.async_shutdown()


async def test_shutdown_cancels_pending_reconnect(hass):
    proto = FakeProtocol(supports_bulk=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    proto.connect_error = OSError("down")
    proto.disconnect_cb(OSError("drop"))
    await coord.async_shutdown()
    count = proto.connect_count
    await _advance(hass)  # no reconnect should fire after shutdown
    assert proto.connect_count == count


async def test_reconnect_aborts_if_shutdown_midflight(hass):
    """If the entry unloads while a reconnect is mid-attempt, it must not
    re-open the link or arm a poll on the torn-down coordinator."""
    proto = FakeProtocol(supports_bulk=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()

    orig_connect = proto.connect

    async def connect_then_shutdown():
        await orig_connect()
        coord._shutting_down = True  # simulate async_shutdown landing mid-attempt

    proto.connect = connect_then_shutdown
    proto.disconnect_cb(OSError("drop"))
    await _advance(hass)  # reconnect fires, connects, then sees shutdown
    assert coord.last_update_success is False  # did NOT mark available
    assert coord._poll_unsub is None  # did NOT arm a poll
    assert proto.connected is False  # cleaned up the opened link


async def test_reconnect_treats_immediate_redrop_as_failure(hass):
    """A link that drops again during the open/prime window isn't a success."""
    proto = FakeProtocol(supports_bulk=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()

    orig_connect = proto.connect

    async def connect_then_die():
        await orig_connect()
        proto.connected = False  # dropped immediately after opening

    proto.connect = connect_then_die
    proto.disconnect_cb(OSError("drop"))
    await _advance(hass)  # attempt: connects then dies -> not success -> retry armed
    assert coord.last_update_success is False

    proto.connect = orig_connect  # link comes back healthy
    await _advance(hass)
    assert coord.last_update_success is True


async def test_safety_poll_not_scheduled_when_shutting_down(hass):
    proto = FakeProtocol(supports_bulk=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass, {OPT_POLL_INTERVAL: 300}), proto)
    await coord.async_init()
    coord._shutting_down = True
    coord._schedule_safety_poll()  # e.g. a poll finishing after shutdown began
    assert coord._poll_unsub is None
