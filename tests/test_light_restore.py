"""A light the bridge has not reported on keeps its last known state.

JetStream has no bulk query: after a restart or reload each load is asked in
turn, and a unit with a weak radio link may not answer. Until it reports, the
light shows what Home Assistant last knew, not "off".
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.centralite.const import (
    CONF_LOAD_IDS,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    OPT_LOAD_NAMES,
    OPT_NONDIMMABLE_LOADS,
    SYSTEM_ELEGANCE,
    SYSTEM_JETSTREAM,
)
from custom_components.centralite.protocol import LoadEvent

from .conftest import FakeProtocol

_JS = "custom_components.centralite.protocol.jetstream.JetStreamProtocol"
_ELG = "custom_components.centralite.protocol.elegance.EleganceProtocol"

KITCHEN = "light.js_kitchen"
HALL = "light.js_hall"


def _entry(hass, system=SYSTEM_JETSTREAM, **options):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SYSTEM_TYPE: system,
            "port": "/dev/ttyUSB0",
            "baud": 19200,
            CONF_LOAD_IDS: [1, 2],
        },
        options={OPT_LOAD_NAMES: {"1": "Kitchen", "2": "Hall"}, **options},
        title="JS",
        unique_id=f"{system}@/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry, proto, target=_JS):
    with patch(target, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)


def _jetstream(levels=None):
    """A JetStream-shaped fake; a load missing from ``levels`` never answers."""
    return FakeProtocol(
        supports_bulk=False, supports_per_load=True, load_levels=levels or {}
    )


async def test_silent_light_keeps_last_state(hass):
    mock_restore_cache(hass, [State(KITCHEN, "on", {"brightness": 162})])
    await _setup(hass, _entry(hass), _jetstream())  # nothing answers

    state = hass.states.get(KITCHEN)
    assert state.state == "on"
    assert state.attributes["brightness"] == 162
    # No history for this one: it still starts off.
    assert hass.states.get(HALL).state == "off"


async def test_a_reply_from_the_bridge_wins_over_the_last_state(hass):
    mock_restore_cache(hass, [State(KITCHEN, "on", {"brightness": 162})])
    await _setup(hass, _entry(hass), _jetstream({1: 0}))
    assert hass.states.get(KITCHEN).state == "off"


async def test_last_state_off_and_unusable_states(hass):
    mock_restore_cache(hass, [State(KITCHEN, "off"), State(HALL, "unavailable")])
    await _setup(hass, _entry(hass), _jetstream())
    assert hass.states.get(KITCHEN).state == "off"
    assert hass.states.get(HALL).state == "off"


async def test_on_off_light_restores_without_brightness(hass):
    mock_restore_cache(hass, [State(HALL, "on")])
    await _setup(hass, _entry(hass, **{OPT_NONDIMMABLE_LOADS: [2]}), _jetstream())
    state = hass.states.get(HALL)
    assert state.state == "on"
    assert state.attributes.get("brightness") is None


async def test_state_survives_a_reload_when_the_light_stays_silent(hass):
    entry = _entry(hass)
    proto = _jetstream()
    with patch(_JS, return_value=proto):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        proto.load_cb(LoadEvent(idx=1, level=63))
        await hass.async_block_till_done()
        assert hass.states.get(KITCHEN).attributes["brightness"] == 162

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(KITCHEN)
    assert state.state == "on"
    assert state.attributes["brightness"] == 162


async def test_bulk_query_result_is_not_overridden(hass):
    """Elegance reads every load at connect; that is newer than any history."""
    mock_restore_cache(hass, [State(KITCHEN, "on", {"brightness": 255})])
    proto = FakeProtocol(bulk_loads={1: False, 2: True})
    await _setup(hass, _entry(hass, system=SYSTEM_ELEGANCE), proto, target=_ELG)
    assert hass.states.get(KITCHEN).state == "off"
    assert hass.states.get(HALL).state == "on"
