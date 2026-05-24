"""Coordinator tests: push handling, scene state, safety poll, disconnect.

These exercise the push-primary state machine that has no standalone coverage:

    protocol push ──> _on_load_event ───┐
                  ──> _on_switch_event ──┼─> coordinator.data ─> entities
                  ──> _on_scene_event ───┘
    activate_scene ─(no scene push)─> commanded state written directly
    reader death ──> _on_disconnect ─> last_update_success = False
"""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.centralite.const import (
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_POLL_INTERVAL,
    SYSTEM_ELEGANCE,
)
from custom_components.centralite.coordinator import CentraliteCoordinator
from custom_components.centralite.protocol import LoadEvent, SceneEvent, SwitchEvent

from .conftest import FakeProtocol


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
