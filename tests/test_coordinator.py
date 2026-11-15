"""Coordinator tests: push handling, scene state, safety poll, disconnect.

These exercise the push-primary state machine that has no standalone coverage:

    protocol push ──> _on_load_event ───┐
                  ──> _on_switch_event ──┼─> coordinator.data ─> entities
                  ──> _on_scene_event ───┘
    activate_scene ─(no scene push)─> commanded state written directly
    reader death ──> _on_disconnect ─> last_update_success = False
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.centralite import coordinator as coordinator_module
from custom_components.centralite.const import (
    CONF_LOAD_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_POLL_INTERVAL,
    OPT_SYNC_CLOCK_ON_CONNECT,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
)
from custom_components.centralite.coordinator import CentraliteCoordinator
from custom_components.centralite.protocol import LoadEvent, SceneEvent, SwitchEvent
from custom_components.centralite.protocol.jetstream import JetStreamProtocol

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
    await coord.async_shutdown()


async def test_no_sync_clock_when_disabled(hass):
    """Default (option absent) must not touch the bridge clock."""
    proto = FakeProtocol(supports_clock=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass), proto)
    await coord.async_init()
    assert not [c for c in proto.calls if c[0] == "set_clock"]
    await coord.async_shutdown()


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
    await coord.async_shutdown()


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
    await coord.async_shutdown()


async def test_safety_poll_not_scheduled_when_shutting_down(hass):
    proto = FakeProtocol(supports_bulk=True, bulk_loads={})
    coord = CentraliteCoordinator(hass, _entry(hass, {OPT_POLL_INTERVAL: 300}), proto)
    await coord.async_init()
    coord._shutting_down = True
    coord._schedule_safety_poll()  # e.g. a poll finishing after shutdown began
    assert coord._poll_unsub is None


# --- Per-load background refresh (JetStream ^F, issue #35) ---
#
#   connect ──> _start_load_refresh ──> background task ──> get_load_level(idx)
#                   (never awaited)                              │ DEV reply
#                                                                ▼
#                                           _on_load_event ──> coordinator.data
#
# The hard requirement: setup and reconnect must never wait on the refresh.


def _js_entry(hass, load_ids):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SYSTEM_TYPE: SYSTEM_JETSTREAM,
            "port": "/dev/ttyUSB0",
            "baud": 19200,
            CONF_LOAD_IDS: load_ids,
        },
        title="JetStream Bridge",
    )
    entry.add_to_hass(hass)
    return entry


def _js_proto(load_levels):
    """A JetStream-shaped fake: no bulk query, one-load-at-a-time level reads."""
    return FakeProtocol(
        supports_bulk=False,
        supports_scene_push=True,
        supports_per_load=True,
        load_levels=load_levels,
    )


async def _drain(hass) -> None:
    """Let the background refresh run to completion."""
    await hass.async_block_till_done(wait_background_tasks=True)


async def _until(predicate) -> None:
    """Yield to the loop until `predicate` holds (the task has reached a point)."""
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


async def test_load_refresh_does_not_block_init(hass):
    """async_init must return while every level query is still outstanding."""
    proto = _js_proto({1: 55, 2: 0})
    proto.query_gate = asyncio.Event()  # hold every query in flight
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1, 2]), proto)

    await coord.async_init()  # returns even though no query can complete

    assert coord.data["loads"] == {}
    assert coord._load_refresh_task is not None
    assert not coord._load_refresh_task.done()

    proto.query_gate.set()
    await _drain(hass)
    assert coord.data["loads"][1] == {"level": 55, "on": True}
    await coord.async_shutdown()


async def test_load_refresh_reads_each_configured_load(hass):
    proto = _js_proto({1: 55, 2: 0, 3: 99})
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1, 2, 3]), proto)
    await coord.async_init()
    await _drain(hass)

    assert proto.load_queries == [1, 2, 3]
    assert coord.data["loads"][1] == {"level": 55, "on": True}
    assert coord.data["loads"][2] == {"level": 0, "on": False}
    assert coord.data["loads"][3] == {"level": 99, "on": True}
    # Every query is bounded by the refresh timeout, not left to a default.
    assert set(proto.query_timeouts) == {coordinator_module.LOAD_REFRESH_TIMEOUT}
    assert coord._load_refresh_task is None  # finished tasks are released
    await coord.async_shutdown()


async def test_load_refresh_skips_silent_load_and_retries_it_once(hass):
    """A device that never answers (a keypad with no load, say) must not
    end the refresh: later loads are still read, and it is re-asked once."""
    proto = _js_proto({1: 10, 3: 30})  # load 2 is silent
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1, 2, 3]), proto)
    await coord.async_init()
    await _drain(hass)

    assert proto.load_queries == [1, 2, 3, 2]  # first pass, then one retry of 2
    assert coord.data["loads"][1] == {"level": 10, "on": True}
    assert coord.data["loads"][3] == {"level": 30, "on": True}
    assert 2 not in coord.data["loads"]
    await coord.async_shutdown()


async def test_load_refresh_retry_pass_recovers_a_dropped_reply(hass):
    """A load silent on the first pass but answering on the retry ends up right."""
    proto = _js_proto({1: 10, 2: 20, 3: 30})
    proto.silent_once = {2}
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1, 2, 3]), proto)
    await coord.async_init()
    await _drain(hass)

    assert proto.load_queries == [1, 2, 3, 2]
    assert coord.data["loads"][2] == {"level": 20, "on": True}
    await coord.async_shutdown()


async def test_load_refresh_stops_when_bridge_never_answers(hass):
    """If the first few queries all go unanswered the bridge isn't replying:
    stop rather than time out on every remaining load (and don't retry)."""
    probe = coordinator_module.LOAD_REFRESH_PROBE_COUNT
    load_ids = list(range(1, probe + 4))
    proto = _js_proto({probe + 2: 50})  # would answer, but is past the probe
    coord = CentraliteCoordinator(hass, _js_entry(hass, load_ids), proto)
    await coord.async_init()
    await _drain(hass)

    assert proto.load_queries == load_ids[:probe]
    assert coord.data["loads"] == {}
    await coord.async_shutdown()


async def test_load_refresh_stops_at_time_budget(hass, monkeypatch):
    monkeypatch.setattr(coordinator_module, "LOAD_REFRESH_BUDGET", 0)
    proto = _js_proto({1: 55, 2: 66})
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1, 2]), proto)
    await coord.async_init()
    await _drain(hass)

    assert proto.load_queries == []  # budget already spent: nothing asked
    await coord.async_shutdown()


async def test_load_refresh_stops_when_link_drops(hass):
    proto = _js_proto({1: 55, 2: 66})
    proto.query_gate = asyncio.Event()
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1, 2]), proto)
    await coord.async_init()
    await _until(lambda: proto.load_queries == [1])

    proto.connected = False  # link gone, before any disconnect callback
    proto.query_gate.set()
    await _drain(hass)

    assert proto.load_queries == [1]  # load 2 never asked on a dead link
    await coord.async_shutdown()


async def test_shutdown_cancels_load_refresh(hass):
    proto = _js_proto({1: 55, 2: 66})
    proto.query_gate = asyncio.Event()
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1, 2]), proto)
    await coord.async_init()
    await _until(lambda: proto.load_queries == [1])
    task = coord._load_refresh_task

    await coord.async_shutdown()
    proto.query_gate.set()
    await _drain(hass)

    assert task.cancelled()
    assert coord._load_refresh_task is None
    assert proto.load_queries == [1]  # nothing asked after shutdown


async def test_disconnect_cancels_load_refresh(hass):
    proto = _js_proto({1: 55, 2: 66})
    proto.query_gate = asyncio.Event()
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1, 2]), proto)
    await coord.async_init()
    await _until(lambda: proto.load_queries == [1])
    task = coord._load_refresh_task

    proto.disconnect_cb(OSError("cable pulled"))
    proto.query_gate.set()
    await _drain(hass)

    assert task.cancelled()
    assert proto.load_queries == [1]
    await coord.async_shutdown()


async def test_reconnect_restores_availability_before_load_refresh(hass):
    """Reconnect must not hold entities unavailable while levels are re-read."""
    proto = _js_proto({1: 55})
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1]), proto)
    await coord.async_init()
    await _drain(hass)
    assert coord.data["loads"][1] == {"level": 55, "on": True}

    proto.load_levels[1] = 0  # the light was switched off while disconnected
    proto.query_gate = asyncio.Event()  # the re-read cannot complete yet
    proto.disconnect_cb(OSError("cable pulled"))
    assert coord.last_update_success is False
    await _advance(hass)  # reconnect timer fires

    assert coord.last_update_success is True  # available again already
    assert coord._load_refresh_task is not None
    assert not coord._load_refresh_task.done()  # refresh still outstanding

    proto.query_gate.set()
    await _drain(hass)
    assert coord.data["loads"][1] == {"level": 0, "on": False}
    await coord.async_shutdown()


async def test_only_one_load_refresh_runs_at_a_time(hass):
    proto = _js_proto({1: 55})
    proto.query_gate = asyncio.Event()
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1]), proto)
    await coord.async_init()
    first = coord._load_refresh_task

    coord._start_load_refresh()  # e.g. a reconnect landing mid-refresh
    second = coord._load_refresh_task
    proto.query_gate.set()
    await _drain(hass)

    assert first is not second
    assert first.cancelled()
    assert second.done() and not second.cancelled()
    await coord.async_shutdown()


async def test_no_load_refresh_for_bulk_query_bridge(hass):
    """Elegance primes from ^G; it must never be queried load by load."""
    proto = FakeProtocol(
        supports_bulk=True, bulk_loads={1: True}, load_levels={1: 55}
    )
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1]), proto)
    await coord.async_init()
    await _drain(hass)

    assert coord._load_refresh_task is None
    assert proto.load_queries == []
    assert coord.data["loads"][1] == {"on": True, "level": 99}  # from bulk prime
    await coord.async_shutdown()


async def test_load_refresh_logs_one_summary_and_no_warnings(hass, caplog):
    """Silent devices are routine: no per-load warning or traceback, just a
    single summary line."""
    proto = _js_proto({1: 10, 3: 30})  # load 2 is silent
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1, 2, 3]), proto)
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.centralite.coordinator"
    ):
        await coord.async_init()
        await _drain(hass)

    ours = [
        r for r in caplog.records
        if r.name == "custom_components.centralite.coordinator"
    ]
    assert not [r for r in ours if r.levelno >= logging.WARNING]
    assert not [r for r in ours if r.exc_info]
    summaries = [r for r in ours if r.levelno == logging.INFO]
    assert len(summaries) == 1
    assert "2 of 3" in summaries[0].getMessage()
    await coord.async_shutdown()


async def test_load_refresh_end_to_end_with_real_jetstream_protocol(hass, monkeypatch):
    """The real protocol, not the fake: ^F goes out on the wire and the DEV
    reply reaches coordinator state through the normal push-event path."""
    monkeypatch.setattr(coordinator_module, "LOAD_REFRESH_TIMEOUT", 0.05)
    reader = asyncio.StreamReader()
    levels = {1: 42, 3: 0}  # device 2 never answers
    sent: list[str] = []

    class _Bridge:
        """Answers ^Fddd with DEVdddll + CRLF, as the hardware does."""

        def write(self, data: bytes) -> None:
            command = data.decode("ascii")
            sent.append(command)
            idx = int(command[2:5])
            if command.startswith("^F") and idx in levels:
                reply = f"DEV{idx:03d}{levels[idx]:02d}".encode("ascii")
                reader.feed_data(reply + bytes((13, 10)))  # CR LF

        async def drain(self) -> None: ...
        def close(self) -> None: ...
        async def wait_closed(self) -> None: ...

    async def factory(url, baudrate):
        return reader, _Bridge()

    proto = JetStreamProtocol(
        "test://", transport_factory=factory, inter_command_delay=0
    )
    coord = CentraliteCoordinator(hass, _js_entry(hass, [1, 2, 3]), proto)
    await coord.async_init()
    await _drain(hass)

    assert sent == ["^F001", "^F002", "^F003", "^F002"]
    assert coord.data["loads"][1] == {"level": 42, "on": True}
    assert coord.data["loads"][3] == {"level": 0, "on": False}
    assert 2 not in coord.data["loads"]
    await coord.async_shutdown()
